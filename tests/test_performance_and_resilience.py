import unittest
from unittest.mock import MagicMock, patch
from collections import deque
import logging

from services.agent_manager import AgentManager
from dashboard.state import MonitorState
from utils.logger import setup_colored_logging
from logging.handlers import RotatingFileHandler

class TestPerformanceAndResilience(unittest.TestCase):

    def test_agent_manager_filters_failed_agents(self):
        """Verified that get_rotational_agents excludes agents that failed authentication."""
        with patch.object(AgentManager, '_load_agents'):
            mgr = AgentManager("credentials.json")
            
            # Setup 3 agents: 1 logged in, 2 failed
            agent_good = MagicMock()
            agent_good.email = "good@tau.ac.il"
            agent_good.is_logged_in = True
            
            agent_bad1 = MagicMock()
            agent_bad1.email = "bad1@tau.ac.il"
            agent_bad1.is_logged_in = False
            
            agent_bad2 = MagicMock()
            agent_bad2.email = "bad2@tau.ac.il"
            agent_bad2.is_logged_in = False
            
            mgr.agents = [agent_good, agent_bad1, agent_bad2]
            
            rotational = mgr.get_rotational_agents()
            self.assertEqual(len(rotational), 1)
            self.assertEqual(rotational[0].email, "good@tau.ac.il")

    def test_agent_manager_fallback_when_none_logged_in(self):
        """If no agents are marked logged in (e.g. initial boot or mock tests), falls back safely."""
        with patch.object(AgentManager, '_load_agents'):
            mgr = AgentManager("credentials.json")
            
            agent1 = MagicMock()
            agent1.email = "a1@tau.ac.il"
            agent1.is_logged_in = False
            
            agent2 = MagicMock()
            agent2.email = "a2@tau.ac.il"
            agent2.is_logged_in = False
            
            mgr.agents = [agent1, agent2]
            
            rotational = mgr.get_rotational_agents()
            self.assertEqual(len(rotational), 2)

    def test_agent_manager_refresh_logins_parallel(self):
        """Verifies refresh_agent_logins calls login on all agents and counts successes."""
        with patch.object(AgentManager, '_load_agents'):
            mgr = AgentManager("credentials.json")
            
            agent1 = MagicMock()
            agent1.login.return_value = True
            agent2 = MagicMock()
            agent2.login.return_value = False
            
            mgr.agents = [agent1, agent2]
            
            successes = mgr.refresh_agent_logins()
            self.assertEqual(successes, 1)
            agent1.login.assert_called_once()
            agent2.login.assert_called_once()

    def test_monitor_state_bounded_deque(self):
        """Verifies that MonitorState bounds log memory to 300 entries without unbound growth."""
        state = MonitorState()
        
        # Add 500 logs
        for i in range(500):
            state.log("INFO", "TEST", f"Message {i}")
            
        snap = state.get_snapshot()
        self.assertLessEqual(len(snap["logs"]), 300)
        # Newest item should be the last inserted
        self.assertEqual(snap["logs"][0]["message"], "Message 499")

    def test_logger_setup_uses_rotating_file_handler(self):
        """Verifies that setup_colored_logging attaches a RotatingFileHandler with size limit."""
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            
        try:
            root = setup_colored_logging(tmp_path)
            rotating_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
            self.assertTrue(len(rotating_handlers) >= 1)
            self.assertEqual(rotating_handlers[0].maxBytes, 10 * 1024 * 1024)
            self.assertEqual(rotating_handlers[0].backupCount, 3)
        finally:
            # Clean up handlers so tmp file can be deleted on Windows
            for h in list(root.handlers):
                root.removeHandler(h)
                h.close()
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_logger_setup_creates_nested_directory(self):
        """Verifies that setup_colored_logging automatically creates missing parent directories."""
        import tempfile
        import os
        import shutil
        tmp_dir = tempfile.mkdtemp()
        nested_log = os.path.join(tmp_dir, "logs", "system.log")
        root = None
        try:
            root = setup_colored_logging(nested_log)
            root.info("Testing nested log write")
            self.assertTrue(os.path.isfile(nested_log))
        finally:
            if root:
                for h in list(root.handlers):
                    root.removeHandler(h)
                    h.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

if __name__ == '__main__':
    unittest.main()
