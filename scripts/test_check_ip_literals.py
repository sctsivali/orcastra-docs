import unittest

from check_ip_literals import violations


def ip(*octets):
    # Built at run time so this file does not trip the check it tests.
    return ".".join(str(o) for o in octets)


VAULT, BENCH, BRIDGE, CGNAT = ip(10, 9, 9, 9), ip(198, 18, 0, 1), ip(10, 9, 9, 0) + "/24", ip(100, 64, 1, 1)


class IpLiteralTests(unittest.TestCase):
    def found(self, text):
        return [lit for _, lit in violations(text)]

    def test_example_ranges_pass(self):
        text = "\n".join([
            "curl http://127.0.0.1:8200/v1/sys/health",
            "listen 0.0.0.0:443 and --host 192.0.2.10",
            "bridge 10.77.0.1/24, docker 172.17.0.0/16, pool 172.20.0.0/14",
            "set_real_ip_from 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16;",
            "nameserver 8.8.8.8. Then open https://192.168.1.50:6969",
        ])
        self.assertEqual(self.found(text), [])

    def test_other_addresses_fail(self):
        text = f"vault at {VAULT}:8200\nhost {BENCH}\nbridge {BRIDGE}\npublic {CGNAT}"
        self.assertEqual(self.found(text), [VAULT, BENCH, BRIDGE, CGNAT])

    def test_reports_line_numbers(self):
        self.assertEqual(list(violations(f"ok\n\nip {VAULT}")), [(3, VAULT)])

    def test_not_addresses(self):
        # OIDs, longer dotted runs and out-of-range octets are not IPv4 literals
        self.assertEqual(self.found(f"oid 1.3.6.1.4.1.2021 and 300.1.1.1 and {VAULT}.9"), [])


if __name__ == "__main__":
    unittest.main()
