import unittest
from desktop_mcp_probe import DesktopMcp


class ReadOnlyBoundaryTests(unittest.TestCase):
    def test_writes_rejected_before_transport(self):
        # No process, connection, network, or model is started by these tests.
        client = DesktopMcp.__new__(DesktopMcp)
        for name in ("move_thread_to_project", "set_thread_title",
                     "create_thread", "move_thread_to_sidebar_section"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                client.request("tools/call", {"name": name, "arguments": {}})

    def test_internal_methods_rejected(self):
        client = DesktopMcp.__new__(DesktopMcp)
        with self.assertRaises(ValueError):
            client.request("thread/metadata/update", {})


if __name__ == "__main__":
    unittest.main()
