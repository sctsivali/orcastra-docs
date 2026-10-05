-- Fluent Bit Lua Filter: Parse nested JSON from Docker log field
-- Docker logs format: {"log": "{\"actual\":\"json\"}\n", "stream": "stdout", "time": "..."}
-- This script parses the JSON in "log" field and merges it to the record

function parse_log_json(tag, timestamp, record)
    -- Get the log field
    local log_field = record["log"]
    
    -- If no log field or not a string, return original record
    if log_field == nil or type(log_field) ~= "string" then
        return 0, timestamp, record
    end
    
    -- Trim whitespace and newlines using pattern matching
    log_field = string.gsub(log_field, "^%s+", "")
    log_field = string.gsub(log_field, "%s+$", "")
    
    -- Check if it looks like JSON (starts with {)
    if string.sub(log_field, 1, 1) ~= "{" then
        return 0, timestamp, record
    end
    
    -- Try to parse as JSON using cjson (built into Fluent Bit)
    local cjson_safe = require("cjson.safe")
    local parsed, err = cjson_safe.decode(log_field)
    
    if parsed and type(parsed) == "table" then
        -- Merge parsed JSON into record
        for key, value in pairs(parsed) do
            record[key] = value
        end
        -- Remove original log field to avoid duplication
        record["log"] = nil
        -- Return modified record (code 1 = record modified)
        return 1, timestamp, record
    else
        -- If not valid JSON, keep original
        return 0, timestamp, record
    end
end
