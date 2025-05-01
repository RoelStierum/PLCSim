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
            self.server = Server()
            self.server.set_endpoint("opc.tcp://0.0.0.0:4860/")
            self.server.set_server_name("PLCSim")
            
            # Register namespace
            self.namespace = self.server.register_namespace("http://plcsim.example.com")
            
            # Get Objects node
            objects = self.server.get_objects_node()
            
            # Create PLC object
            self.plc = objects.add_object(self.namespace, "PLC")
            
            # Create variables
            self.variables = {
                "xWatchDog": self.plc.add_variable(self.namespace, "xWatchDog", False),
                "xStopServer": self.plc.add_variable(self.namespace, "xStopServer", False),
                "iStatus": self.plc.add_variable(self.namespace, "iStatus", 0),
                "iTaskType": self.plc.add_variable(self.namespace, "iTaskType", 0),
                "iOrigin": self.plc.add_variable(self.namespace, "iOrigin", 0),
                "iDestination": self.plc.add_variable(self.namespace, "iDestination", 0),
                "iError": self.plc.add_variable(self.namespace, "iError", 0),
                "iErrorCode": self.plc.add_variable(self.namespace, "iErrorCode", 0),
                "iErrorText": self.plc.add_variable(self.namespace, "iErrorText", ""),
                "iMode": self.plc.add_variable(self.namespace, "iMode", 0),
                # Log variables
                "sLastJob": self.plc.add_variable(self.namespace, "sLastJob", ""),
                "sJobHistory": self.plc.add_variable(self.namespace, "sJobHistory", ""),
                "sErrorHistory": self.plc.add_variable(self.namespace, "sErrorHistory", ""),
                "iTotalJobs": self.plc.add_variable(self.namespace, "iTotalJobs", 0),
                "iTotalErrors": self.plc.add_variable(self.namespace, "iTotalErrors", 0)
            }
            
            # Set variables to be writable
            for var in self.variables.values():
                var.set_writable()
            
            # Start server
            self.server.start()
            logger.info("Server started at opc.tcp://0.0.0.0:4860/")
            
            return True
            
        except Exception as e:
            logger.error(f"Error initializing server: {e}")
            return False

    def run(self):
        """Run the server and handle watchdog updates"""
        try:
            self.running = True
            counter = 0
            
            while self.running:
                # Check if stop flag is set
                if self.variables["xStopServer"].get_value():
                    logger.info("Stop flag detected, shutting down server...")
                    self.stop()
                    break
                
                # Update watchdog
                counter += 1
                self.variables["xWatchDog"].set_value(not self.variables["xWatchDog"].get_value())
                logger.info(f"[WATCHDOG] Updated: {self.variables['xWatchDog'].get_value()} (counter: {counter})")
                
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Error in run loop: {e}")
            self.stop()

    def stop(self):
        """Stop the server gracefully"""
        try:
            logger.info("[STOP] Stopping server...")
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