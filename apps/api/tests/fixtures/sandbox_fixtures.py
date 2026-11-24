"""Trusted, minimal Python test fixtures for sandbox execution verification."""

from repopilot_api.fixture_registry import (
    CROSS_FILE_SHOP_FILES,
    CROSS_FILE_SHOP_FIX_PATCH,
    CROSS_FILE_SHOP_REGRESSION_PATCH,
)

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

# Deterministic demo fixture with regression tests and reproducible bug in power()
DEMO_CALCULATOR_CODE: str = (
    "def add(a: int, b: int) -> int:\n"
    '    """Return sum of a and b."""\n'
    "    return a + b\n\n\n"
    "def multiply(a: int, b: int) -> int:\n"
    '    """Return product of a and b."""\n'
    "    return a * b\n\n\n"
    "def divide(a: int, b: int) -> float:\n"
    '    """Return quotient of a and b."""\n'
    "    if b == 0:\n"
    '        raise ValueError("Division by zero")\n'
    "    return a / b\n\n\n"
    "def power(base: int, exponent: int) -> int:\n"
    '    """Return base raised to exponent."""\n'
    "    # Bug: returns multiplication instead of exponentiation\n"
    "    return base * exponent\n"
)

DEMO_TEST_CALCULATOR_CODE: str = (
    "import unittest\n"
    "from src.calculator import add, divide, multiply, power\n\n\n"
    "class TestCalculator(unittest.TestCase):\n"
    "    # Regression tests (must pass in baseline and continue passing)\n"
    "    def test_add(self):\n"
    "        self.assertEqual(add(2, 3), 5)\n"
    "        self.assertEqual(add(-1, 1), 0)\n\n"
    "    def test_multiply(self):\n"
    "        self.assertEqual(multiply(3, 4), 12)\n"
    "        self.assertEqual(multiply(0, 5), 0)\n\n"
    "    def test_divide_normal(self):\n"
    "        self.assertEqual(divide(10, 2), 5.0)\n\n"
    "    def test_divide_by_zero(self):\n"
    "        with self.assertRaises(ValueError):\n"
    "            divide(10, 0)\n\n"
    "    # Bug reproduction tests (fail in baseline, pass after fix)\n"
    "    def test_power_positive(self):\n"
    "        self.assertEqual(power(2, 3), 8)\n\n"
    "    def test_power_zero(self):\n"
    "        self.assertEqual(power(5, 0), 1)\n\n\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)

DEMO_FIXTURE_FILES: dict[str, str] = {
    "src/calculator.py": DEMO_CALCULATOR_CODE,
    "tests/test_calculator.py": DEMO_TEST_CALCULATOR_CODE,
}

DEMO_CALC_FIX_PATCH: str = (
    "--- a/src/calculator.py\n"
    "+++ b/src/calculator.py\n"
    "@@ -18,4 +18,4 @@\n"
    " def power(base: int, exponent: int) -> int:\n"
    '     """Return base raised to exponent."""\n'
    "     # Bug: returns multiplication instead of exponentiation\n"
    "-    return base * exponent\n"
    "+    return base ** exponent\n"
)

DEMO_CALC_REGRESSION_PATCH: str = (
    "--- a/src/calculator.py\n"
    "+++ b/src/calculator.py\n"
    "@@ -6,3 +6,3 @@\n"
    " def multiply(a: int, b: int) -> int:\n"
    '     """Return product of a and b."""\n'
    "-    return a * b\n"
    "+    return a + b\n"
    "@@ -18,4 +18,4 @@\n"
    " def power(base: int, exponent: int) -> int:\n"
    '     """Return base raised to exponent."""\n'
    "     # Bug: returns multiplication instead of exponentiation\n"
    "-    return base * exponent\n"
    "+    return base ** exponent\n"
)

DEMO_CALC_FAILED_PATCH: str = (
    "--- a/src/calculator.py\n"
    "+++ b/src/calculator.py\n"
    "@@ -18,4 +18,4 @@\n"
    " def power(base: int, exponent: int) -> int:\n"
    '     """Return base raised to exponent."""\n'
    "     # Bug: returns multiplication instead of exponentiation\n"
    "-    return base * exponent\n"
    "+    return base + exponent\n"
)

DEMO_CALC_SYNTAX_ERROR_PATCH: str = (
    "--- a/src/calculator.py\n"
    "+++ b/src/calculator.py\n"
    "@@ -18,4 +18,4 @@\n"
    " def power(base: int, exponent: int) -> int:\n"
    '     """Return base raised to exponent."""\n'
    "     # Bug: returns multiplication instead of exponentiation\n"
    "-    return base * exponent\n"
    "+    return base **\n"
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

RANGE_VALIDATOR_FIXTURE_FILES: dict[str, str] = {
    "src/validator.py": (
        "def is_in_range(val: int, min_val: int, max_val: int) -> bool:\n"
        '    """Check if val is within [min_val, max_val] inclusive."""\n'
        "    # Bug: uses exclusive check instead of inclusive on upper bound\n"
        "    return min_val <= val < max_val\n\n\n"
        "def is_positive(val: int) -> bool:\n"
        '    """Check if val is positive."""\n'
        "    return val > 0\n"
    ),
    "tests/test_validator.py": (
        "import unittest\n"
        "from src.validator import is_in_range, is_positive\n\n\n"
        "class TestValidator(unittest.TestCase):\n"
        "    def test_positive(self):\n"
        "        self.assertTrue(is_positive(5))\n"
        "        self.assertFalse(is_positive(-1))\n\n"
        "    def test_in_range_middle(self):\n"
        "        self.assertTrue(is_in_range(5, 1, 10))\n\n"
        "    def test_in_range_lower_bound(self):\n"
        "        self.assertTrue(is_in_range(1, 1, 10))\n\n"
        "    def test_out_of_range_above(self):\n"
        "        self.assertFalse(is_in_range(11, 1, 10))\n\n"
        "    def test_in_range_upper_bound(self):\n"
        "        self.assertTrue(is_in_range(10, 1, 10))\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    ),
}

RANGE_VALIDATOR_FIX_PATCH: str = (
    "--- a/src/validator.py\n"
    "+++ b/src/validator.py\n"
    "@@ -3,2 +3,2 @@\n"
    "     # Bug: uses exclusive check instead of inclusive on upper bound\n"
    "-    return min_val <= val < max_val\n"
    "+    return min_val <= val <= max_val\n"
)

RANGE_VALIDATOR_REGRESSION_PATCH: str = (
    "--- a/src/validator.py\n"
    "+++ b/src/validator.py\n"
    "@@ -4,6 +4,6 @@\n"
    "-    return min_val <= val < max_val\n"
    "+    return min_val <= val <= max_val\n"
    " \n"
    " \n"
    " def is_positive(val: int) -> bool:\n"
    '     """Check if val is positive."""\n'
    "-    return val > 0\n"
    "+    return val < 0\n"
)

__all__ = [
    "CROSS_FILE_SHOP_FILES",
    "CROSS_FILE_SHOP_FIX_PATCH",
    "CROSS_FILE_SHOP_REGRESSION_PATCH",
    "PASSING_FIXTURE_FILES",
    "FAILING_FIXTURE_FILES",
    "CALC_FIX_PATCH",
    "DEMO_FIXTURE_FILES",
    "DEMO_CALC_FIX_PATCH",
    "DEMO_CALC_REGRESSION_PATCH",
    "DEMO_CALC_FAILED_PATCH",
    "DEMO_CALC_SYNTAX_ERROR_PATCH",
    "RANGE_VALIDATOR_FIXTURE_FILES",
    "RANGE_VALIDATOR_FIX_PATCH",
    "RANGE_VALIDATOR_REGRESSION_PATCH",
]
