#!/bin/bash
# ==============================================================================
# Orcastra OpenSearch Dashboards Setup
# Imports index patterns and dashboard templates into OpenSearch Dashboards
#
# Usage:
#   ./setup_opensearch_dashboards.sh [OPTIONS]
#
# Options:
#   -u, --url         OpenSearch Dashboards URL (default: http://localhost:5601)
#   -U, --user        Admin username (default: admin)
#   -P, --password    Admin password (required, or set OPENSEARCH_ADMIN_PASSWORD)
#   -d, --dashboard-dir  Directory containing ndjson files
#                        (default: ../config/opensearch-dashboards)
#   -h, --help        Show this help message
#
# Environment Variables:
#   OPENSEARCH_ADMIN_PASSWORD  Admin password (alternative to -P flag)
#   DASHBOARDS_URL             Dashboards URL (alternative to -u flag)
# ==============================================================================

set -euo pipefail

# colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $1"; }

# defaults
DASHBOARDS_URL="${DASHBOARDS_URL:-http://localhost:5601}"
ADMIN_USER="admin"
ADMIN_PASS="${OPENSEARCH_ADMIN_PASSWORD:-}"
DASHBOARD_DIR=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# index pattern IDs — must match the references in ndjson files
ACCESS_PATTERN_ID="12ba9640-0014-11f1-b2de-a9a1dde61479"
AUDIT_PATTERN_ID="34b2e040-0014-11f1-b2de-a9a1dde61479"
OVERVIEW_PATTERN_ID="orcastra-all-logs-pattern"

usage() {
    head -20 "$0" | grep "^#" | sed 's/^# \?//'
    exit 0
}

# parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -u|--url)        DASHBOARDS_URL="$2"; shift 2 ;;
        -U|--user)       ADMIN_USER="$2"; shift 2 ;;
        -P|--password)   ADMIN_PASS="$2"; shift 2 ;;
        -d|--dashboard-dir) DASHBOARD_DIR="$2"; shift 2 ;;
        -h|--help)       usage ;;
        *)               log_error "Unknown option: $1"; usage ;;
    esac
done

# resolve dashboard directory
if [ -z "$DASHBOARD_DIR" ]; then
    # try relative to script location
    if [ -d "$SCRIPT_DIR/../config/opensearch-dashboards" ]; then
        DASHBOARD_DIR="$SCRIPT_DIR/../config/opensearch-dashboards"
    elif [ -d "./config/opensearch-dashboards" ]; then
        DASHBOARD_DIR="./config/opensearch-dashboards"
    else
        log_error "Cannot find dashboard ndjson directory. Use -d to specify."
        exit 1
    fi
fi

DASHBOARD_DIR="$(cd "$DASHBOARD_DIR" && pwd)"

# validate password
if [ -z "$ADMIN_PASS" ]; then
    log_error "Admin password is required. Use -P flag or set OPENSEARCH_ADMIN_PASSWORD."
    exit 1
fi

# validate ndjson files exist
NDJSON_FILES=(
    "access-logs-dashboard-v3.ndjson"
    "audit-logs-dashboard-v3.ndjson"
    "logs-overview-dashboard.ndjson"
    "vault-audit-dashboard.ndjson"
)

for f in "${NDJSON_FILES[@]}"; do
    if [ ! -f "$DASHBOARD_DIR/$f" ]; then
        log_error "Missing dashboard file: $DASHBOARD_DIR/$f"
        exit 1
    fi
done

echo ""
echo "=========================================="
echo "  Orcastra OpenSearch Dashboards Import"
echo "=========================================="
echo "  URL:       $DASHBOARDS_URL"
echo "  User:      $ADMIN_USER"
echo "  Source:     $DASHBOARD_DIR"
echo "  Dashboards: ${#NDJSON_FILES[@]} files"
echo "=========================================="
echo ""

# ------------------------------------------------------------------
# Step 1: Wait for OpenSearch Dashboards to be ready
# ------------------------------------------------------------------
log_step "Waiting for OpenSearch Dashboards to be ready..."

max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" \
        -u "$ADMIN_USER:$ADMIN_PASS" \
        "$DASHBOARDS_URL/api/status" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "200" ]; then
        log_info "OpenSearch Dashboards is ready (HTTP $HTTP_CODE)"
        break
    fi

    attempt=$((attempt + 1))
    printf "  Attempt %d/%d (HTTP %s)...\r" "$attempt" "$max_attempts" "$HTTP_CODE"
    sleep 5
done

if [ $attempt -eq $max_attempts ]; then
    log_error "OpenSearch Dashboards not reachable at $DASHBOARDS_URL after $((max_attempts * 5))s"
    exit 1
fi

# ------------------------------------------------------------------
# Step 2: Create index patterns
# ------------------------------------------------------------------
log_step "Creating index patterns..."

create_index_pattern() {
    local pattern_id="$1"
    local pattern_title="$2"
    local time_field="${3:-@timestamp}"

    local payload
    payload=$(cat <<EOJSON
{
    "attributes": {
        "title": "$pattern_title",
        "timeFieldName": "$time_field"
    }
}
EOJSON
    )

    local response
    response=$(curl -sk -w "\n%{http_code}" \
        -u "$ADMIN_USER:$ADMIN_PASS" \
        -X POST "$DASHBOARDS_URL/api/saved_objects/index-pattern/$pattern_id?overwrite=true" \
        -H "osd-xsrf: true" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)

    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [ "$http_code" = "200" ] || [ "$http_code" = "409" ]; then
        log_info "  Index pattern '$pattern_title' (id=$pattern_id) — OK"
        return 0
    else
        log_warn "  Index pattern '$pattern_title' — HTTP $http_code"
        log_warn "  Response: $body"
        return 1
    fi
}

create_index_pattern "$ACCESS_PATTERN_ID"  "orcastra-access-*"
create_index_pattern "$AUDIT_PATTERN_ID"   "orcastra-audit-*"
create_index_pattern "$OVERVIEW_PATTERN_ID" "orcastra-*"

# ------------------------------------------------------------------
# Step 3: Import dashboard ndjson files
# ------------------------------------------------------------------
log_step "Importing dashboard templates..."

import_dashboard() {
    local file="$1"
    local filename
    filename=$(basename "$file")

    local response
    response=$(curl -sk -w "\n%{http_code}" \
        -u "$ADMIN_USER:$ADMIN_PASS" \
        -X POST "$DASHBOARDS_URL/api/saved_objects/_import?overwrite=true" \
        -H "osd-xsrf: true" \
        -F "file=@$file" 2>/dev/null)

    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    # check for success
    if echo "$body" | grep -q '"success":true'; then
        local count
        count=$(echo "$body" | grep -o '"successCount":[0-9]*' | head -1 | cut -d: -f2)
        log_info "  $filename — imported $count objects"
        return 0
    elif [ "$http_code" = "200" ]; then
        # partial success — some objects may have errors
        local errors
        errors=$(echo "$body" | grep -o '"errors":\[.*\]' | head -1 || echo "")
        if [ -n "$errors" ]; then
            log_warn "  $filename — partial import (some objects already exist)"
        else
            log_info "  $filename — imported (HTTP $http_code)"
        fi
        return 0
    else
        log_error "  $filename — FAILED (HTTP $http_code)"
        log_error "  Response: $body"
        return 1
    fi
}

import_ok=0
import_fail=0

for f in "${NDJSON_FILES[@]}"; do
    if import_dashboard "$DASHBOARD_DIR/$f"; then
        import_ok=$((import_ok + 1))
    else
        import_fail=$((import_fail + 1))
    fi
done

# ------------------------------------------------------------------
# Step 4: Set default index pattern
# ------------------------------------------------------------------
log_step "Setting default index pattern to orcastra-access-*..."

curl -sk \
    -u "$ADMIN_USER:$ADMIN_PASS" \
    -X POST "$DASHBOARDS_URL/api/opensearch-dashboards/settings" \
    -H "osd-xsrf: true" \
    -H "Content-Type: application/json" \
    -d "{\"changes\":{\"defaultIndex\":\"$ACCESS_PATTERN_ID\"}}" \
    > /dev/null 2>&1 && log_info "  Default index pattern set" \
    || log_warn "  Could not set default index pattern"

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo "=========================================="
echo "  Import Complete"
echo "=========================================="
echo "  Succeeded: $import_ok / ${#NDJSON_FILES[@]}"
if [ $import_fail -gt 0 ]; then
    echo -e "  ${RED}Failed:    $import_fail${NC}"
fi
echo ""
echo "  Dashboards available at:"
echo "    $DASHBOARDS_URL/app/dashboards"
echo ""
echo "  Imported dashboards:"
echo "    - Orcastra Logs Overview"
echo "    - Orcastra Access Logs"
echo "    - Orcastra Activity & Audit Logs"
echo "    - Vault Security Audit"
echo "=========================================="
