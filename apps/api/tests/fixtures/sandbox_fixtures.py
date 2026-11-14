"""Trusted, minimal Python test fixtures for sandbox execution verification."""

PASSING_FIXTURE_FILES: dict[str, str] = {
    "calc.py": ('def add(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b\n'),
    "test_calc.py": (
        "import unittest\n"
        "from calc import add\n\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
        "        self.assertEqual(add(-1, 1), 0)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

FAILING_FIXTURE_FILES: dict[str, str] = {
    "calc.py": (
        "def add(a: int, b: int) -> int:\n"
        '    """Add two numbers."""\n'
        "    return a - b  # Bug: returns subtraction instead of addition\n"
    ),
    "test_calc.py": (
        "import unittest\n"
        "from calc import add\n\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
        "        self.assertEqual(add(-1, 1), 0)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

CALC_FIX_PATCH: str = (
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,3 +1,3 @@\n"
    " def add(a: int, b: int) -> int:\n"
    '     """Add two numbers."""\n'
    "-    return a - b  # Bug: returns subtraction instead of addition\n"
    "+    return a + b\n"
)

NETWORK_PROBE_FILES: dict[str, str] = {
    "test_network.py": (
        "import socket\n"
        "import unittest\n\n\n"
        "class TestNetworkIsolation(unittest.TestCase):\n"
        "    def test_outbound_network_blocked(self):\n"
        "        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "        sock.settimeout(1.0)\n"
        "        with self.assertRaises(OSError):\n"
        "            sock.connect(('8.8.8.8', 53))\n"
        "        sock.close()\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
}

NON_ROOT_PROBE_FILES: dict[str, str] = {
    "test_user.py": (
        "import os\n"
        "import unittest\n\n\n"
        "class TestNonRootUser(unittest.TestCase):\n"
        "    def test_user_is_not_root(self):\n"
        "        uid = os.getuid()\n"
        "        self.assertNotEqual(uid, 0, 'Security violation: running as root!')\n"
        "        self.assertGreaterEqual(uid, 1000, f'Expected non-root uid, got {uid}')\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
}

READ_ONLY_PROBE_FILES: dict[str, str] = {
    "test_readonly.py": (
        "import unittest\n\n\n"
        "class TestReadOnlyFilesystem(unittest.TestCase):\n"
        "    def test_cannot_write_to_workspace(self):\n"
        "        with self.assertRaises(OSError):\n"
        "            with open('/workspace/unauthorized.txt', 'w') as f:\n"
        "                f.write('malicious write')\n\n"
        "    def test_cannot_write_to_rootfs(self):\n"
        "        with self.assertRaises(OSError):\n"
        "            with open('/etc/unauthorized.txt', 'w') as f:\n"
        "                f.write('malicious write')\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
}

WRITABLE_TMP_PROBE_FILES: dict[str, str] = {
    "test_tmpfs.py": (
        "import os\n"
        "import unittest\n\n\n"
        "class TestTmpfsBehavior(unittest.TestCase):\n"
        "    def test_can_write_to_tmp(self):\n"
        "        path = '/tmp/sandbox_scratch.txt'\n"
        "        with open(path, 'w') as f:\n"
        "            f.write('ephemeral data')\n"
        "        self.assertTrue(os.path.exists(path))\n"
        "        with open(path, 'r') as f:\n"
        "            self.assertEqual(f.read(), 'ephemeral data')\n"
        "        os.remove(path)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
}

TIMEOUT_PROBE_FILES: dict[str, str] = {
    "test_timeout.py": (
        "import time\n"
        "import unittest\n\n\n"
        "class TestExecutionTimeout(unittest.TestCase):\n"
        "    def test_sleep_hang(self):\n"
        "        time.sleep(30)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
}

OUTPUT_FLOOD_PROBE_FILES: dict[str, str] = {
    "test_flood.py": (
        "import sys\n"
        "import unittest\n\n\n"
        "class TestOutputFlood(unittest.TestCase):\n"
        "    def test_flood(self):\n"
        "        for _ in range(2000):\n"
        "            sys.stdout.write('Flood output line testing bounded stream capture.\\n')\n"
        "        sys.stdout.flush()\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
}
