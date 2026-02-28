import logging
import time
import threading
import concurrent.futures
from typing import List, Dict, Any, Tuple
from core.entities import BookingRequest, ActionLogDetails
from core.enums import BookingResult, ActionType, ActionStatus
from services.agent_manager import AgentManager
from clients.tau_client import TauClient
import config

logger = logging.getLogger(__name__)

class BookingManager:
    def __init__(self, agent_manager: AgentManager):
        self.agent_manager = agent_manager
        self.failed_rooms = set()

    def attempt_booking(self, request: BookingRequest, stop_event: threading.Event = None, preferred_room_id: int = None, progress_callback=None) -> Tuple[BookingResult, Any]:
        """
        Attempts to book the request using available agents and strategies.
        If preferred_room_id is provided, tries that room first.
        If progress_callback is provided, calls it with the agent_email before attempting.
        Returns (Result, Details).
        """
        agents = self.agent_manager.get_rotational_agents() # Todo: Pass last successful user preference
        
        # Room Batches - Place preferred room at the very start of the first batch
        high_priority = list(config.HIGH_PRIORITY_ROOMS.keys())
        low_priority = list(config.LOW_PRIORITY_ROOMS.keys())
        
        if preferred_room_id:
            # Remove it from where it might already be so we avoid duplicates
            if preferred_room_id in high_priority: high_priority.remove(preferred_room_id)
            if preferred_room_id in low_priority: low_priority.remove(preferred_room_id)
            # Insert at the absolute front
            high_priority.insert(0, preferred_room_id)

        room_batches = [high_priority, low_priority]
        
        start_time = time.time()
        self.failed_rooms.clear() # Reset per attempt
        
        exhausted_emails = set()

        # Retry Loop (Sniping / Persistence)
        while True:
            # Quick Exit if stopped
            if stop_event and stop_event.is_set():
                log = ActionLogDetails(action=ActionType.BOOK, status=ActionStatus.INFO, message="Stop signal received. Aborting booking attempt.")
                logger.info(str(log))
                return BookingResult.ERROR, "Systems Stopping"

            # Timestamp Check
            if time.time() - start_time > config.BOOKING_TIMEOUT_SECONDS:
                log = ActionLogDetails(action=ActionType.BOOK, status=ActionStatus.FAILED, start_time=request.utc_start, end_time=request.utc_end, reason="Timeout", message=f"Timeout for {request.summary}")
                logger.error(str(log))
                return BookingResult.ERROR, "Timeout"

            # Check if all rooms failed
            total_rooms = sum(len(b) for b in room_batches)
            if len(self.failed_rooms) >= total_rooms:
                log = ActionLogDetails(action=ActionType.BOOK, status=ActionStatus.FAILED, start_time=request.utc_start, end_time=request.utc_end, reason="All Rooms Taken", message=f"All rooms failed for {request.summary}")
                logger.warning(str(log))
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
                        
                        if progress_callback:
                            progress_callback(agent.email)

                        # res, ref_num_or_error = agent.book_room(...)
                        res, details = agent.book_room(rid, request.utc_start, request.utc_end)

                        if res == BookingResult.SUCCESS:
                            log = ActionLogDetails(action=ActionType.BOOK, status=ActionStatus.SUCCESS, user=agent.email, room=rname, ref_num=details, start_time=request.utc_start, end_time=request.utc_end, message=f"Successfully booked {request.summary}")
                            logger.info(str(log))
                            # Return success details: (SUCCESS, (agent_email, room_name, ref_num))
                            return BookingResult.SUCCESS, (agent.email, rname, details)
                        
                        elif res == BookingResult.ROOM_TAKEN:
                            # Room is taken for this specific slot.
                            self.failed_rooms.add(rid)
                            break # Move to next room
                            
                        elif res == BookingResult.USER_LIMIT:
                            exhausted_emails.add(agent.email)
                            break # Try next agent
                        
                        elif res in [BookingResult.TOO_EARLY, BookingResult.CLOSED]:
                            # Fatal errors for this slot
                            return res, details
                        
                        elif res == BookingResult.ERROR:
                            # Log and try next? Or failing hard?
                            # If it's a login failure, we might want to try next agent.
                            # If it's a server error, maybe all agents will fail.
                            if "Login Failed" in str(details):
                                 exhausted_emails.add(agent.email)
                                 continue
                            
                            log = ActionLogDetails(action=ActionType.BOOK, status=ActionStatus.FAILED, user=agent.email, room=rname, start_time=request.utc_start, end_time=request.utc_end, reason=str(details), message=f"Error for {request.summary}")
                            logger.error(str(log))
                            # Continue to next agent/room?
                            pass
            
            # Sleep briefly to avoid hammering if we are looping (e.g. waiting for slot)
            time.sleep(config.BOOKING_RETRY_INTERVAL_SECONDS)

    def cancel_booking(self, request: Any) -> Tuple[bool, str]: # request: DeletionRequest
        """
        Attempts to cancel a booking.
        """
        # Finds the agent for this booking owner
        agent_to_use = None
        if request.owner_email:
            agent_to_use = self.agent_manager.get_agent(request.owner_email)
        
        agents_to_try = [agent_to_use] if agent_to_use else self.agent_manager.get_all_agents()
        
        last_reason = "No agents available"
        
        for agent in agents_to_try:
            log = ActionLogDetails(action=ActionType.DELETE, status=ActionStatus.INFO, user=agent.email, ref_num=request.ref_num, message=f"Attempting delete with {agent.email}")
            logger.info(str(log))
            # Force Login for delete safety
            agent.is_logged_in = False 
            
            success, reason = agent.delete_booking(request.ref_num)
            if success:
                 return True, "Deleted"
            
            last_reason = reason
                 
        return False, last_reason
