from opcua import Server, ua
import logging
import signal
import sys
import socket
import time

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def is_port_in_use(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(('localhost', port))
            if result == 0:
                logger.info(f"Port {port} is in use")
                return True
            else:
                logger.info(f"Port {port} is available (error code: {result})")
                return False
    except Exception as e:
        logger.error(f"Error checking port {port}: {e}")
        return False

class PLCTest:
    def __init__(self):
        self.server = None
        self.namespace = None
        self.variables = {}
        self.watchdog_counter = 0
        self.watchdog_value = False
        self.server_url = "opc.tcp://127.0.0.1:4860"
        logger.info("[INIT] PLCTest initialized with URL: %s", self.server_url)

    def initialize(self):
        """Initialize the OPC UA server and create variables"""
        try:
            # Create server
            logger.info("[INIT] Creating OPC UA server...")
            self.server = Server()
            
            # Set endpoint
            endpoint = "opc.tcp://0.0.0.0:4860/"
            logger.info(f"[INIT] Setting endpoint to {endpoint}")
            self.server.set_endpoint(endpoint)
            self.server.set_server_name("PLCSim")
            
            # Register namespace
            uri = "http://plcsim.example.com"
            logger.info(f"[INIT] Registering namespace: {uri}")
            self.namespace = self.server.register_namespace(uri)
            logger.info(f"[INIT] Namespace index: {self.namespace}")
            
            # Get Objects node
            logger.info("[INIT] Getting Objects node")
            objects = self.server.get_objects_node()
            
            # Create PLC object
            logger.info("[INIT] Creating PLC object")
            self.plc = objects.add_object(self.namespace, "PLC")
            logger.info(f"[INIT] PLC node created: {self.plc}")
            
            # Create variables according to interface document
            logger.info("[INIT] Creating variables...")
            self.variables = {
                # System status variables
                "iMainStatus": self.plc.add_variable(self.namespace, "iMainStatus", 0),  # 1=Semi-auto, 2=Auto, 3=Teach, 4=Manual
                "xWatchDog": self.plc.add_variable(self.namespace, "xWatchDog", False),
                "xStopServer": self.plc.add_variable(self.namespace, "xStopServer", False),
                
                # Job status variables
                "iStatus": self.plc.add_variable(self.namespace, "iStatus", 0),  # 0=IDLE, 1=BUSY, 2=ERROR, 3=COMPLETED, etc
                "iTaskType": self.plc.add_variable(self.namespace, "iTaskType", 0),  # 0=Reset, 1=Full Placement, etc
                "iOrigin": self.plc.add_variable(self.namespace, "iOrigin", 0),
                "iDestination": self.plc.add_variable(self.namespace, "iDestination", 0),
                
                # Error handling
                "iError": self.plc.add_variable(self.namespace, "iError", 0),
                "iErrorCode": self.plc.add_variable(self.namespace, "iErrorCode", 0),
                "sShortAlarmDescription": self.plc.add_variable(self.namespace, "sShortAlarmDescription", ""),
                "sAlarmMessage": self.plc.add_variable(self.namespace, "sAlarmMessage", ""),
                "sAlarmSolution": self.plc.add_variable(self.namespace, "sAlarmSolution", ""),
                
                # Station status
                "iStationStatus": self.plc.add_variable(self.namespace, "iStationStatus", 0),
                "xTrayInElevator": self.plc.add_variable(self.namespace, "xTrayInElevator", False),
                
                # Job control
                "xAcknowledgeMovement": self.plc.add_variable(self.namespace, "xAcknowledgeMovement", False),
                "iCancelReason": self.plc.add_variable(self.namespace, "iCancelReason", 0),
                
                # Logging
                "sLastJob": self.plc.add_variable(self.namespace, "sLastJob", ""),
                "sJobHistory": self.plc.add_variable(self.namespace, "sJobHistory", ""),
                "sErrorHistory": self.plc.add_variable(self.namespace, "sErrorHistory", ""),
                "iTotalJobs": self.plc.add_variable(self.namespace, "iTotalJobs", 0),
                "iTotalErrors": self.plc.add_variable(self.namespace, "iTotalErrors", 0)
            }
            
            # Set variables to be writable
            logger.info("[INIT] Setting variables as writable")
            for name, var in self.variables.items():
                var.set_writable()
                logger.info(f"[INIT] Variable {name} created and set writable")
            
            # Start server
            logger.info("[INIT] Starting server...")
            self.server.start()
            logger.info("[INIT] Server started successfully")
            
            # Log available namespaces
            namespaces = self.server.get_namespace_array()
            logger.info("[INIT] Available namespaces:")
            for idx, ns in enumerate(namespaces):
                logger.info(f"[INIT]   {idx}: {ns}")
            
            return True
            
        except Exception as e:
            logger.error(f"[INIT] Error initializing server: {e}", exc_info=True)
            return False

    def run(self):
        """Run the server and handle job flows"""
        try:
            self.running = True
            counter = 0
            
            while self.running:
                # Check if stop flag is set
                if self.variables["xStopServer"].get_value():
                    logger.info("[STOP] Stop flag detected, shutting down server...")
                    self.stop()
                    break
                
                # Update watchdog
                counter += 1
                self.variables["xWatchDog"].set_value(not self.variables["xWatchDog"].get_value())
                logger.info(f"[WATCHDOG] Updated: {self.variables['xWatchDog'].get_value()} (counter: {counter})")
                
                # Handle job flows
                self._handle_job_flow()
                
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Error in run loop: {e}")
            self.stop()

    def _handle_job_flow(self):
        """Handle different job flows based on task type"""
        try:
            status = self.variables["iStatus"].get_value()
            task_type = self.variables["iTaskType"].get_value()
            
            # Only process if status is BUSY (1)
            if status == 1:
                if task_type == 1:  # Full Placement Job
                    self._handle_full_placement_job()
                elif task_type == 2:  # Move To Job
                    self._handle_move_to_job()
                elif task_type == 4:  # Bring Away Job
                    self._handle_bring_away_job()
                elif task_type == 0:  # Reset Job
                    self._handle_reset_job()
            
        except Exception as e:
            logger.error(f"[FLOW] Error handling job flow: {e}")
            self._set_error_state(888, "Error in job flow", str(e))

    def _handle_full_placement_job(self):
        """Handle Full Placement Job flow according to interface document"""
        try:
            # 1. Wait for ecosystem to set task parameters
            if not self._wait_for_task_parameters():
                return

            # 2. Set status to WAIT_ECOSYSTEM (20)
            self.variables["iStatus"].set_value(20)
            logger.info("[FLOW] Full Placement: Waiting for ecosystem acknowledgment")

            # 3. Wait for ecosystem to acknowledge movement (xAcknowledgeMovement)
            if not self._wait_for_movement_acknowledgment():
                return

            # 4. Move to origin position
            logger.info("[FLOW] Full Placement: Moving to origin")
            self.variables["xTrayInElevator"].set_value(False)
            time.sleep(1)  # Simulate movement

            # 5. Set status to WAIT_ECOSYSTEM (20) for pickup
            self.variables["iStatus"].set_value(20)
            logger.info("[FLOW] Full Placement: Waiting for pickup acknowledgment")

            # 6. Wait for ecosystem to acknowledge pickup
            if not self._wait_for_movement_acknowledgment():
                return

            # 7. Pick up tray
            logger.info("[FLOW] Full Placement: Picking up tray")
            self.variables["xTrayInElevator"].set_value(True)
            time.sleep(1)  # Simulate pickup

            # 8. Set status to WAIT_ECOSYSTEM (20) for move to destination
            self.variables["iStatus"].set_value(20)
            logger.info("[FLOW] Full Placement: Waiting for move acknowledgment")

            # 9. Wait for ecosystem to acknowledge movement
            if not self._wait_for_movement_acknowledgment():
                return

            # 10. Move to destination
            logger.info("[FLOW] Full Placement: Moving to destination")
            time.sleep(1)  # Simulate movement

            # 11. Set status to WAIT_ECOSYSTEM (20) for dropoff
            self.variables["iStatus"].set_value(20)
            logger.info("[FLOW] Full Placement: Waiting for dropoff acknowledgment")

            # 12. Wait for ecosystem to acknowledge dropoff
            if not self._wait_for_movement_acknowledgment():
                return

            # 13. Drop off tray
            logger.info("[FLOW] Full Placement: Dropping off tray")
            self.variables["xTrayInElevator"].set_value(False)
            time.sleep(1)  # Simulate dropoff

            # 14. Complete job
            logger.info("[FLOW] Full Placement: Completing job")
            self.variables["iStatus"].set_value(3)  # COMPLETED
            
        except Exception as e:
            logger.error(f"[FLOW] Error in Full Placement Job: {e}")
            self._set_error_state(888, "Error in Full Placement Job", str(e))

    def _handle_move_to_job(self):
        """Handle Move To Job flow according to interface document"""
        try:
            # 1. Wait for ecosystem to set task parameters
            if not self._wait_for_task_parameters():
                return

            # 2. Set status to WAIT_ECOSYSTEM (20)
            self.variables["iStatus"].set_value(20)
            logger.info("[FLOW] Move To: Waiting for ecosystem acknowledgment")

            # 3. Wait for ecosystem to acknowledge movement
            if not self._wait_for_movement_acknowledgment():
                return

            # 4. Move to destination
            logger.info("[FLOW] Move To: Moving to destination")
            time.sleep(2)  # Simulate movement

            # 5. Complete job
            logger.info("[FLOW] Move To: Completing job")
            self.variables["iStatus"].set_value(3)  # COMPLETED
            
        except Exception as e:
            logger.error(f"[FLOW] Error in Move To Job: {e}")
            self._set_error_state(888, "Error in Move To Job", str(e))

    def _handle_bring_away_job(self):
        """Handle Bring Away Job flow according to interface document"""
        try:
            # 1. Wait for ecosystem to set task parameters
            if not self._wait_for_task_parameters():
                return

            # 2. Set status to WAIT_ECOSYSTEM (20)
            self.variables["iStatus"].set_value(20)
            logger.info("[FLOW] Bring Away: Waiting for ecosystem acknowledgment")

            # 3. Wait for ecosystem to acknowledge movement
            if not self._wait_for_movement_acknowledgment():
                return

            # 4. Move to destination with tray
            logger.info("[FLOW] Bring Away: Moving to destination")
            self.variables["xTrayInElevator"].set_value(True)
            time.sleep(2)  # Simulate movement

            # 5. Set status to WAIT_ECOSYSTEM (20) for dropoff
            self.variables["iStatus"].set_value(20)
            logger.info("[FLOW] Bring Away: Waiting for dropoff acknowledgment")

            # 6. Wait for ecosystem to acknowledge dropoff
            if not self._wait_for_movement_acknowledgment():
                return

            # 7. Drop off tray
            logger.info("[FLOW] Bring Away: Dropping off tray")
            self.variables["xTrayInElevator"].set_value(False)
            time.sleep(1)  # Simulate dropoff

            # 8. Complete job
            logger.info("[FLOW] Bring Away: Completing job")
            self.variables["iStatus"].set_value(3)  # COMPLETED
            
        except Exception as e:
            logger.error(f"[FLOW] Error in Bring Away Job: {e}")
            self._set_error_state(888, "Error in Bring Away Job", str(e))

    def _handle_reset_job(self):
        """Handle Reset Job flow"""
        try:
            # 1. Move to home position (-2)
            logger.info("[FLOW] Reset: Moving to home position")
            time.sleep(1)  # Simulate movement
            
            # 2. Complete job
            logger.info("[FLOW] Reset: Completing job")
            self.variables["iStatus"].set_value(3)  # COMPLETED
            
        except Exception as e:
            logger.error(f"[FLOW] Error in Reset Job: {e}")
            self._set_error_state(888, "Error in Reset Job", str(e))

    def _wait_for_task_parameters(self):
        """Wait for ecosystem to set task parameters"""
        timeout = 30  # 30 second timeout
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if (self.variables["iOrigin"].get_value() != 0 or 
                self.variables["iDestination"].get_value() != 0):
                return True
            time.sleep(0.1)
        
        logger.error("[FLOW] Timeout waiting for task parameters")
        self._set_error_state(888, "Task Parameter Timeout", "Ecosystem did not set task parameters")
        return False

    def _wait_for_movement_acknowledgment(self):
        """Wait for ecosystem to acknowledge movement"""
        timeout = 30  # 30 second timeout
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.variables["xAcknowledgeMovement"].get_value():
                # Reset acknowledgment
                self.variables["xAcknowledgeMovement"].set_value(False)
                return True
            time.sleep(0.1)
        
        logger.error("[FLOW] Timeout waiting for movement acknowledgment")
        self._set_error_state(888, "Acknowledgment Timeout", "Ecosystem did not acknowledge movement")
        return False

    def _set_error_state(self, error_code, short_desc, message):
        """Set error state with provided details"""
        try:
            self.variables["iError"].set_value(error_code)
            self.variables["iErrorCode"].set_value(error_code)
            self.variables["sShortAlarmDescription"].set_value(short_desc)
            self.variables["sAlarmMessage"].set_value(message)
            self.variables["sAlarmSolution"].set_value("Check system and reset")
            self.variables["iStatus"].set_value(2)  # ERROR
            self.variables["iStationStatus"].set_value(error_code)
            logger.error(f"[ERROR] {short_desc}: {message}")
        except Exception as e:
            logger.error(f"[ERROR] Failed to set error state: {e}")

    def stop(self):
        """Stop the server gracefully"""
        try:
            logger.info("[STOP] Stopping server...")
            self.running = False
            self.server.stop()
            logger.info("[STOP] Server stopped successfully")
        except Exception as e:
            logger.error(f"[STOP] Error stopping server: {str(e)}")
        finally:
            sys.exit(0)

    def log_job(self, job_type, origin, destination, status):
        """Log a job to the PLC history"""
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            job_info = f"[{timestamp}] {job_type}: {origin} -> {destination} ({status})"
            
            # Update last job
            self.variables["sLastJob"].set_value(job_info)
            
            # Update job history (keep last 10 jobs)
            current_history = self.variables["sJobHistory"].get_value()
            new_history = f"{job_info}\n{current_history}"
            if new_history.count('\n') > 10:  # Keep only last 10 entries
                new_history = '\n'.join(new_history.split('\n')[:10])
            self.variables["sJobHistory"].set_value(new_history)
            
            # Update total jobs counter
            total_jobs = self.variables["iTotalJobs"].get_value()
            self.variables["iTotalJobs"].set_value(total_jobs + 1)
            
            logger.info(f"Logged job: {job_info}")
        except Exception as e:
            logger.error(f"Error logging job: {e}")

    def log_error(self, error_code, error_text):
        """Log an error to the PLC history"""
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            error_info = f"[{timestamp}] Error {error_code}: {error_text}"
            
            # Update error history (keep last 10 errors)
            current_history = self.variables["sErrorHistory"].get_value()
            new_history = f"{error_info}\n{current_history}"
            if new_history.count('\n') > 10:  # Keep only last 10 entries
                new_history = '\n'.join(new_history.split('\n')[:10])
            self.variables["sErrorHistory"].set_value(new_history)
            
            # Update total errors counter
            total_errors = self.variables["iTotalErrors"].get_value()
            self.variables["iTotalErrors"].set_value(total_errors + 1)
            
            logger.info(f"Logged error: {error_info}")
        except Exception as e:
            logger.error(f"Error logging error: {e}")

def signal_handler(sig, frame):
    logger.info("[SIGNAL] Received shutdown signal, stopping server...")
    if plc and plc.server:
        plc.stop()
    sys.exit(0)

def main():
    global plc
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    plc = PLCTest()
    try:
        plc.initialize()
        logger.info("[MAIN] PLC test simulator initialized successfully")
        plc.run()
    except KeyboardInterrupt:
        logger.info("[MAIN] Shutting down server...")
        if plc.server:
            plc.stop()
    except Exception as e:
        logger.error(f"[MAIN] Error in main: {str(e)}")
        if plc.server:
            try:
                plc.stop()
            except:
                pass

if __name__ == "__main__":
    plc = None
    main() 