import json
import logging
import os
import concurrent.futures
from typing import List, Optional, Dict
from clients.tau_client import TauClient
from core.entities import UserCredentials

logger = logging.getLogger(__name__)

class AgentManager:
    def __init__(self, credentials_file: str):
        self.credentials_file = credentials_file
        self.agents: List[TauClient] = []
        self._load_agents()

    def _load_agents(self):
        if not os.path.exists(self.credentials_file):
            logger.warning(f"Credentials file not found: {self.credentials_file}")
            return

        try:
            with open(self.credentials_file, 'r') as f:
                data = json.load(f)
                credentials = [UserCredentials(**u) for u in data]
                
            logger.info(f"[AGENT] Initializing {len(credentials)} agents...")
            
            temp_agents = [TauClient(c) for c in credentials]
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                future_to_agent = {executor.submit(a.login): a for a in temp_agents}
                for future in concurrent.futures.as_completed(future_to_agent):
                    agent = future_to_agent[future]
                    try:
                        success = future.result()
                    except Exception as e:
                        logger.error(f"[AGENT] Login thread error for {agent.email}: {e}")
                        success = False
                    
                    # Store agent regardless of success, so we can show "Failed" in dashboard
                    self.agents.append(agent)
                    
                    if not success:
                         logger.warning(f"[AGENT] Failed to login: {agent.email}")

            logger.info(f"[AGENT] {len(self.agents)} Agents Initialized.")
            
        except Exception as e:
            logger.error(f"[AGENT] Failed to load agents: {e}")

    def get_agent(self, email: str) -> Optional[TauClient]:
        return next((a for a in self.agents if a.email.lower() == email.lower()), None)

    def get_all_agents(self) -> List[TauClient]:
        return self.agents



    def refresh_agent_logins(self) -> int:
        """Refreshes login sessions for all agents in parallel. Returns count of successful logins."""
        if not self.agents:
            return 0
        success_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(self.agents))) as executor:
            future_to_agent = {executor.submit(a.login): a for a in self.agents}
            for future in concurrent.futures.as_completed(future_to_agent):
                agent = future_to_agent[future]
                try:
                    if future.result():
                        success_count += 1
                except Exception as e:
                    logger.error(f"[AGENT] Refresh login error for {agent.email}: {e}")
        return success_count

    def get_rotational_agents(self, preferred_email: Optional[str] = None) -> List[TauClient]:
        """
        Returns list of active agents, prioritizing logged-in agents and preferred_email.
        """
        logged_in = [a for a in self.agents if getattr(a, 'is_logged_in', False)]
        active = logged_in if logged_in else list(self.agents)
        if preferred_email:
            priority = next((a for a in active if a.email == preferred_email), None)
            if priority:
                active.remove(priority)
                active.insert(0, priority)
                
        # Return agents in order, prioritizing preferred_email if given
        return active
