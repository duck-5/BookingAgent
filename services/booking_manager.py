import logging
import time
import concurrent.futures
from typing import List, Dict, Any, Tuple
from core.entities import BookingRequest
from core.enums import BookingResult
from services.agent_manager import AgentManager
from clients.tau_client import TauClient
import config

logger = logging.getLogger(__name__)

class BookingManager:
    def __init__(self, agent_manager: AgentManager):
        self.agent_manager = agent_manager
        self.failed_rooms = set()

    def attempt_booking(self, request: BookingRequest) -> Tuple[BookingResult, Any]:
        """
        Attempts to book the request using available agents and strategies.
        Returns (Result, Details).
        """
        agents = self.agent_manager.get_rotational_agents() # Todo: Pass last successful user preference
        
        # Room Batches
        room_batches = [
            list(config.HIGH_PRIORITY_ROOMS.keys()), 
            list(config.LOW_PRIORITY_ROOMS.keys())
        ]
        
        start_time = time.time()
        self.failed_rooms.clear() # Reset per attempt
        
        exhausted_emails = set()

        # Retry Loop (Sniping / Persistence)
        while True:
            # Timestamp Check
            if time.time() - start_time > 120:
                logger.info(f"[BOOKING] Timeout for {request.summary}")
                return BookingResult.ERROR, "Timeout"

            # Check if all rooms failed
            total_rooms = sum(len(b) for b in room_batches)
            if len(self.failed_rooms) >= total_rooms:
                logger.warning(f"[BOOKING] All rooms failed for {request.summary}")
                return BookingResult.ROOM_TAKEN, "All Rooms Taken"

            # Iterate Rooms
            for batch_name, rooms in zip(["HIGH", "LOW"], room_batches):
                for rid in rooms:
                    if rid in self.failed_rooms: continue
                    rname = config.ALL_ROOMS[rid]

                    # Iterate Agents
                    for agent in agents:
                        if agent.email in exhausted_emails: continue

                        # Try Booking
                        # Note: We rely on TauClient to handle specific error codes 
                        # but we need to interpret them here to maybe mark room as failed?
                        # TauClient returns (Result, ref_num)
                        
                        res, ref_num = agent.book_room(rid, request.utc_start, request.utc_end)

                        if res == BookingResult.SUCCESS:
                            # Return success details: (SUCCESS, (agent_email, room_name, ref_num))
                            return BookingResult.SUCCESS, (agent.email, rname, ref_num)
                        
                        elif res == BookingResult.ROOM_TAKEN:
                            # Room is taken for this specific slot. Mark it as failed so we don't try it with other agents.
                            # BUT be careful: maybe it's taken for THIS user (limit)? No, "conflicting" usually means room busy.
                            self.failed_rooms.add(rid)
                            break # Move to next room
                            
                        elif res == BookingResult.USER_LIMIT:
                            exhausted_emails.add(agent.email)
                            break # Try next agent for SAME room (if possible, actually if user limit, we just skip user)
                        
                        elif res == BookingResult.TOO_EARLY:
                            # If it's too early, NO agent can book.
                            # We might want to wait? Or just return.
                            # For now, treat as error.
                            pass
            
            # Sleep briefly to avoid hammering if we are looping (e.g. waiting for slot)
            time.sleep(1)

    def cancel_booking(self, request: Any) -> bool: # request: DeletionRequest
        """
        Attempts to cancel a booking.
        """
        # Finds the agent for this booking owner
        agent_to_use = None
        if request.owner_email:
            agent_to_use = self.agent_manager.get_agent(request.owner_email)
        
        agents_to_try = [agent_to_use] if agent_to_use else self.agent_manager.get_all_agents()
        
        for agent in agents_to_try:
            logger.info(f"[BOOKING] Attempting delete {request.ref_num} with {agent.email}...")
            # Force Login for delete safety
            agent.is_logged_in = False 
            if agent.delete_booking(request.ref_num):
                 return True
                 
        return False
