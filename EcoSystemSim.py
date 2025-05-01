import tkinter as tk
from tkinter import ttk, messagebox, Canvas
from opcua import Client, ua
import threading
import queue
from functools import partial
import logging
import re
import random
import socket
import subprocess
import time
import sys
import os

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_port_available(port):
    """Check if a port is available using netstat"""
    try:
        # First try a direct socket connection
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(('127.0.0.1', port))
            if result == 0:
                logger.info(f"Port {port} is open and accepting connections")
                return True
            else:
                logger.warning(f"Port {port} is not accepting connections (error code: {result})")
                return False
    except Exception as e:
        logger.error(f"Error checking port {port}: {e}")
        return False

class DataChangeHandler:
    def __init__(self, message_queue, nodeid_map):
        self.message_queue = message_queue
        self.nodeid_map = nodeid_map

    def datachange_notification(self, node, val, data):
        try:
            # Get node ID string
            node_id_str = str(node.nodeid)
            
            # Try to get variable name from nodeid map
            if node_id_str in self.nodeid_map:
                var_name = self.nodeid_map[node_id_str]
                logger.info(f"Data change notification: {var_name} = {val}")
                self.message_queue.put(("update", {"variable_name": var_name, "value": val}))
            else:
                # Fallback - use the node ID string
                logger.info(f"Data change notification (fallback): {node_id_str} = {val}")
                self.message_queue.put(("update", {node_id_str: val}))
                
        except Exception as e:
            logger.error(f"Error in datachange handler: {e}")
            try:
                # Last resort fallback
                self.message_queue.put(("update", {str(node): val}))
            except Exception as e2:
                logger.error(f"Error in fallback handler: {e2}")

class EcoSystemSimulator:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Ecosystem Simulator")
        self.root.geometry("900x900")
        
        # OPC-UA client setup
        self.client = None
        self.namespace = None
        self.variables = {}
        self.nodeid_map = {}  # Mapping between nodeId and variable names
        self.message_queue = queue.Queue()
        
        # Server connection settings
        self.server_url = "opc.tcp://127.0.0.1:4860"
        self.connection_timeout = 30
        self.keepalive_interval = 10000
        self.keepalive_count = 3
        
        # Animation state lock
        self._animation_lock = threading.Lock()
        self._is_animating = False
        
        # Valid job types and their corresponding task types
        self.valid_job_types = {
            "Full Placement": 1,
            "Move To": 2,
            "Bring Away": 4
        }
        
        # Valid location ranges
        self.even_locs = list(range(100, -3, -2))
        self.odd_locs = list(range(99, -2, -2))
        self.all_locs = self.even_locs + self.odd_locs
        
        # Error simulation settings
        self.error_simulation_chance = 0.1  # Configurable error chance
        
        # Translation maps for values
        self.system_mode_map = {
            0: "MANUAL",
            1: "AUTOMATIC", 
            2: "MAINTENANCE"
        }
        
        self.task_type_map = {
            0: "NONE",
            1: "PICK",
            2: "PLACE",
            4: "MOVE"
        }
        
        self.status_map = {
            0: "IDLE",
            1: "BUSY",
            2: "ERROR",
            3: "COMPLETED",
            20: "WAIT_ECOSYSTEM",
            550: "CANCELLED"
        }
        
        # Cancel reasons with safe fallback
        self.cancel_reasons = {
            1: "Pickup assignment while tray is on forks",
            2: "Destination out of reach",
            3: "Origin out of reach",
            4: "Destination and origin can't be zero with a full move operation / Origin can't be zero with a prepare or move operation",
            5: "Lifts cross each other",
            6: "Invalid assignment"
        }
        
        # Lift state
        self.current_position = -2
        self.lift_moving = False
        
        # Mode
        self.mode = tk.StringVar(value="Handmatig")
        self.automaat_running = False
        
        # Setup logging to file
        self.setup_file_logging()
        
        # Create UI elements
        self.create_ui()
        
        # Start OPC-UA client in a separate thread
        self.client_thread = threading.Thread(target=self.run_client, daemon=True)
        self.client_thread.start()

    def setup_file_logging(self):
        """Setup logging to CSV files"""
        try:
            # Create logs directory if it doesn't exist
            if not os.path.exists('logs'):
                os.makedirs('logs')
            
            # Setup job log
            self.job_log_file = 'logs/job_history.csv'
            if not os.path.exists(self.job_log_file):
                with open(self.job_log_file, 'w') as f:
                    f.write('timestamp,job_type,origin,destination,status\n')
            
            # Setup error log
            self.error_log_file = 'logs/error_history.csv'
            if not os.path.exists(self.error_log_file):
                with open(self.error_log_file, 'w') as f:
                    f.write('timestamp,error_code,error_text\n')
            
            logger.info("File logging setup completed")
        except Exception as e:
            logger.error(f"Error setting up file logging: {e}")

    def log_job_to_file(self, job_type, origin, destination, status):
        """Log job to CSV file"""
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self.job_log_file, 'a') as f:
                f.write(f'{timestamp},{job_type},{origin},{destination},{status}\n')
        except Exception as e:
            logger.error(f"Error logging job to file: {e}")

    def log_error_to_file(self, error_code, error_text):
        """Log error to CSV file"""
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self.error_log_file, 'a') as f:
                f.write(f'{timestamp},{error_code},{error_text}\n')
        except Exception as e:
            logger.error(f"Error logging error to file: {e}")

    def create_ui(self):
        # Mode selectie bovenaan
        mode_frame = ttk.Frame(self.root)
        mode_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(mode_frame, text="Mode:").pack(side="left")
        self.mode_select = ttk.Combobox(mode_frame, textvariable=self.mode, values=["Handmatig", "Automaat"], width=10, state="readonly")
        self.mode_select.pack(side="left", padx=5)
        self.mode_select.bind("<<ComboboxSelected>>", self.on_mode_change)
        
        # Main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side="right", fill="both", expand=True, padx=5, pady=5)
        
        # Status Frame
        status_frame = ttk.LabelFrame(left_frame, text="PLC Status", padding="5")
        status_frame.pack(fill="x", padx=5, pady=5)
        
        # Status labels
        self.status_labels = {}
        statuses = [
            ("iMainStatus", "System Mode:"),
            ("iStatus", "Current Status:"),
            ("iTaskType", "Task Type:"),
            ("iStationStatus", "Station Status:"),
            ("xWatchDog", "Watchdog:"),
            ("xTrayInElevator", "Tray in Elevator:")
        ]
        
        for i, (var_name, label_text) in enumerate(statuses):
            ttk.Label(status_frame, text=label_text).grid(row=i//2, column=i%2*2, padx=5, pady=2, sticky="e")
            self.status_labels[var_name] = ttk.Label(status_frame, text="--")
            self.status_labels[var_name].grid(row=i//2, column=i%2*2+1, padx=5, pady=2, sticky="w")

        # Alarm Frame
        alarm_frame = ttk.LabelFrame(left_frame, text="Alarm Information", padding="5")
        alarm_frame.pack(fill="x", padx=5, pady=5)
        
        self.alarm_labels = {}
        alarms = [
            ("sShortAlarmDescription", "Short Description:"),
            ("sAlarmMessage", "Alarm Message:"),
            ("sAlarmSolution", "Solution:")
        ]
        
        for i, (var_name, label_text) in enumerate(alarms):
            ttk.Label(alarm_frame, text=label_text).grid(row=i, column=0, padx=5, pady=2, sticky="e")
            self.alarm_labels[var_name] = ttk.Label(alarm_frame, text="--")
            self.alarm_labels[var_name].grid(row=i, column=1, padx=5, pady=2, sticky="w")

        # Control Frame
        control_frame = ttk.LabelFrame(left_frame, text="Job Control", padding="5")
        control_frame.pack(fill="x", padx=5, pady=5)
        
        # Job type selection
        ttk.Label(control_frame, text="Job Type:").grid(row=0, column=0, padx=5, pady=5)
        self.job_type = ttk.Combobox(control_frame, values=["Full Placement", "Move To", "Bring Away"], state="readonly")
        self.job_type.grid(row=0, column=1, padx=5, pady=5)
        self.job_type.set("Full Placement")
        
        # Origin and destination inputs
        ttk.Label(control_frame, text="Origin:").grid(row=1, column=0, padx=5, pady=5)
        self.origin_var = tk.StringVar(value="2")
        self.origin_entry = ttk.Entry(control_frame, textvariable=self.origin_var)
        self.origin_entry.grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(control_frame, text="Destination:").grid(row=2, column=0, padx=5, pady=5)
        self.destination_var = tk.StringVar(value="50")
        self.destination_entry = ttk.Entry(control_frame, textvariable=self.destination_var)
        self.destination_entry.grid(row=2, column=1, padx=5, pady=5)
        
        # Buttons
        button_frame = ttk.Frame(control_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=10)
        
        self.start_button = ttk.Button(button_frame, text="Start Job", command=self.start_job)
        self.start_button.pack(side="left", padx=5)
        self.cancel_button = ttk.Button(button_frame, text="Cancel Job", command=self.cancel_job)
        self.cancel_button.pack(side="left", padx=5)
        self.error_button = ttk.Button(button_frame, text="Send Error", command=self.send_error)
        self.error_button.pack(side="left", padx=5)
        self.refresh_button = ttk.Button(button_frame, text="Refresh Values", command=self.refresh_values)
        self.refresh_button.pack(side="left", padx=5)
        self.reset_button = ttk.Button(button_frame, text="Reset", command=self.reset_lift)
        self.reset_button.pack(side="left", padx=5)
        self.stop_button = ttk.Button(button_frame, text="Stop PLC Sim", command=self.stop_plc_sim)
        self.stop_button.pack(side="left", padx=5)

        # Connection status
        self.connection_label = ttk.Label(left_frame, text="Status: Disconnected", foreground="red")
        self.connection_label.pack(pady=5)
        
        # Lift Visualization
        lift_frame = ttk.LabelFrame(right_frame, text="Lift Visualization", padding="5")
        lift_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Canvas hoogte automatisch schalen
        self.canvas = Canvas(lift_frame, width=300, height=900, bg="lightgray")
        self.canvas.pack(padx=10, pady=10, fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw_simple_lift(self.lift_loc_idx if hasattr(self, 'lift_loc_idx') else 250, getattr(self, 'lift_side', 'mid'), getattr(self, 'tray_on_lift', False)))
        
        # Draw the lift structure
        self.draw_simple_lift(250, 'mid', False)
        
        # Disable all controls initially
        self.set_controls_state("disabled")

    def set_controls_state(self, state):
        """Enable or disable all controls based on connection state"""
        self.mode_select.configure(state="disabled" if state == "disabled" else "readonly")
        self.job_type.configure(state="disabled" if state == "disabled" else "readonly")
        self.origin_entry.configure(state=state)
        self.destination_entry.configure(state=state)
        self.start_button.configure(state=state)
        self.cancel_button.configure(state=state)
        self.error_button.configure(state=state)
        self.refresh_button.configure(state=state)
        self.reset_button.configure(state=state)
        self.stop_button.configure(state=state)
        
        # Update all status labels to show disconnected state
        for label in self.status_labels.values():
            label.configure(text="--")
        for label in self.alarm_labels.values():
            label.configure(text="--")

    def draw_simple_lift(self, loc_idx, lift_side, tray_on_lift, tray_y=None):
        self.canvas.delete("all")
        left_x = 20
        mid_x = 120
        right_x = 220
        schacht_top = 30
        even_locs = list(range(100, -3, -2))
        odd_locs = list(range(99, -2, -2))
        canvas_height = self.canvas.winfo_height()
        block_h = max(10, int((canvas_height - 60) / len(even_locs)))
        # Even locaties links
        for i, loc in enumerate(even_locs):
            y = schacht_top + i * block_h
            self.canvas.create_rectangle(left_x, y, left_x+40, y+block_h-2, fill="#e0e0e0", outline="gray")
            self.canvas.create_text(left_x+20, y+block_h/2, text=str(loc), font=("Arial", 8))
        # Oneven locaties rechts
        for i, loc in enumerate(odd_locs):
            y = schacht_top + i * block_h
            self.canvas.create_rectangle(right_x, y, right_x+40, y+block_h-2, fill="#e0e0e0", outline="gray")
            self.canvas.create_text(right_x+20, y+block_h/2, text=str(loc), font=("Arial", 8))
        # Liftschacht
        self.canvas.create_rectangle(mid_x, schacht_top, mid_x+40, schacht_top+len(even_locs)*block_h, outline="blue", width=2)
        # Lift
        y = schacht_top + loc_idx * block_h
        lift_x = mid_x if lift_side == 'mid' else (left_x if lift_side == 'left' else right_x)
        self.canvas.create_rectangle(lift_x, y, lift_x+40, y+block_h-2, fill="green", outline="black", width=2)
        # Tray
        if tray_on_lift:
            tray_x = lift_x+5
            tray_y = y+3 if tray_y is None else tray_y
            self.canvas.create_rectangle(tray_x, tray_y, tray_x+30, tray_y+block_h-8, fill="orange", outline="black", width=2)

    def connect_to_plc(self):
        """Connect to the PLC simulator"""
        try:
            # Create client with specific settings
            self.client = Client(url=self.server_url)
            self.client.timeout = self.connection_timeout
            self.client.session_keepalive = self.keepalive_interval
            self.client.session_keepalive_count = self.keepalive_count
            
            logger.debug("[CONN] Client settings:")
            logger.debug(f"  URL: {self.server_url}")
            logger.debug(f"  Timeout: {self.client.timeout}")
            logger.debug(f"  Keepalive: {self.client.session_keepalive}")
            logger.debug(f"  Keepalive count: {self.client.session_keepalive_count}")
            
            # Connect to server
            logger.info("[CONN] Attempting to connect to %s", self.server_url)
            self.client.connect()
            logger.info("[CONN] Connected to server")
            
            # Get namespace index
            uri = "http://plcsim.example.com"
            self.namespace = self.client.get_namespace_index(uri)
            logger.info(f"[CONN] Namespace index for '{uri}': {self.namespace}")
            
            # Get PLC node
            logger.info(f"Getting PLC object with namespace index {self.namespace}...")
            plc_node = self.client.nodes.root.get_child(["0:Objects", f"{self.namespace}:PLC"])
            logger.info(f"Found PLC node: {plc_node}")
            
            # Get all variables
            var_names = ["iMainStatus", "xWatchDog", "iStatus", "iTaskType", "iOrigin", 
                       "iDestination", "iError", "iErrorCode", "iErrorText", "iMode",
                       "sShortAlarmDescription", "sAlarmMessage", "sAlarmSolution",
                       "iStationStatus", "xTrayInElevator"]  # Added missing variables
            
            logger.info("Getting variable nodes...")
            for var_name in var_names:
                try:
                    node = plc_node.get_child([f"{self.namespace}:{var_name}"])
                    self.variables[var_name] = node
                    # Store node ID to variable name mapping
                    node_id_str = str(node.nodeid)
                    self.nodeid_map[node_id_str] = var_name
                    logger.info(f"Found variable: {var_name}")
                    logger.info(f"  - Node ID: {node_id_str}")
                except Exception as var_error:
                    logger.error(f"Error getting variable {var_name}: {var_error}")
                    logger.error(f"  - Node path attempted: {self.namespace}:{var_name}")
            
            # Read initial values
            self.read_all_values()
            
            # Start subscription
            handler = DataChangeHandler(self.message_queue, self.nodeid_map)
            subscription = self.client.create_subscription(500, handler)
            
            # Subscribe to each variable individually
            for var_name, var_node in self.variables.items():
                subscription.subscribe_data_change(var_node)
                logger.info(f"Subscribed to {var_name}")
            
            logger.info("Subscription created and variables monitored")
            self.message_queue.put(("connection", "Connected"))
            
        except Exception as e:
            logger.error(f"Error connecting to PLC: {e}", exc_info=True)
            self.message_queue.put(("error", f"Connection error: {e}"))
            if self.client:
                try:
                    logger.debug("Attempting to disconnect client...")
                    self.client.disconnect()
                    logger.debug("Client disconnected")
                except Exception as disconnect_error:
                    logger.error(f"Error during disconnect: {disconnect_error}")
                self.client = None

    def read_all_values(self):
        """Read all variable values and update UI"""
        for var_name, node in self.variables.items():
            try:
                value = node.get_value()
                logger.info(f"Read initial value for {var_name}: {value}")
                self.message_queue.put(("update", {"variable_name": var_name, "value": value}))
            except Exception as e:
                logger.error(f"Error reading value for {var_name}: {e}")

    def refresh_values(self):
        """Manually refresh all values from the PLC"""
        self.read_all_values()

    def run_client(self):
        """Run the OPC UA client in a separate thread"""
        try:
            self.connect_to_plc()
        except Exception as e:
            logger.error(f"[CLIENT] Failed to set up client thread: {e}")
            self.message_queue.put(("error", f"Client thread error: {e}"))

    def update_ui(self, data):
        # Check if data is a dictionary with variable_name and value
        if isinstance(data, dict) and "variable_name" in data and "value" in data:
            var_name = data["variable_name"]
            value = data["value"]
            
            # Handle special cases for current position
            if var_name == "iLiftPosition":
                self.current_position = value
                self.draw_simple_lift(250, 'mid', False)
                return
                
            # Apply value translation for specific variables
            display_value = value
            if var_name == "iMainStatus" and value in self.system_mode_map:
                display_value = f"{value} ({self.system_mode_map[value]})"
            elif var_name == "iTaskType" and value in self.task_type_map:
                display_value = f"{value} ({self.task_type_map[value]})"
            elif var_name == "iStatus" and value in self.status_map:
                display_value = f"{value} ({self.status_map[value]})"
            elif var_name == "xTrayInElevator":
                display_value = "Yes" if value else "No"
            elif var_name == "iStationStatus":
                display_value = str(value)
                
            if var_name in self.status_labels:
                self.status_labels[var_name].config(text=str(display_value))
                # Force update of task type display
                if var_name == "iTaskType":
                    self.root.update_idletasks()
            elif var_name in self.alarm_labels:
                self.alarm_labels[var_name].config(text=str(display_value))
                
            # Update lift state based on status and task type
            if var_name == "iTaskType" and value > 0:
                self.lift_moving = True
                if hasattr(self, 'lift_loc_idx'):
                    self.draw_simple_lift(self.lift_loc_idx, getattr(self, 'lift_side', 'mid'), getattr(self, 'tray_on_lift', False))
            elif var_name == "iStatus" and value == 3:  # Completed
                self.lift_moving = False
                # Update position based on destination
                try:
                    dest = int(self.destination_var.get())
                    self.current_position = dest
                    if hasattr(self, 'lift_loc_idx'):
                        self.draw_simple_lift(self.lift_loc_idx, getattr(self, 'lift_side', 'mid'), getattr(self, 'tray_on_lift', False))
                except ValueError:
                    pass

    def validate_job_inputs(self, job_type, origin, destination):
        """Validate job inputs before starting a job"""
        try:
            # Validate job type
            if job_type not in self.valid_job_types:
                raise ValueError(f"Invalid job type: {job_type}")
            
            # Validate origin and destination
            if origin not in self.all_locs:
                raise ValueError(f"Invalid origin: {origin}")
            if destination not in self.all_locs:
                raise ValueError(f"Invalid destination: {destination}")
            
            # Additional validation for specific job types
            if job_type == "Full Placement":
                if origin == destination:
                    raise ValueError("Origin and destination cannot be the same for Full Placement")
            
            return True
        except ValueError as e:
            logger.error(f"Job validation failed: {e}")
            self.show_error("Invalid Job", str(e))
            return False

    def show_error(self, title, message, force_show=False):
        """Show error message, optionally skipping in automaat mode"""
        if not self.automaat_running or force_show:
            messagebox.showerror(title, message)

    def get_location_index(self, location):
        """Safely get the index of a location in either even or odd locations"""
        try:
            if location in self.even_locs:
                return self.even_locs.index(location)
            elif location in self.odd_locs:
                return self.odd_locs.index(location)
            else:
                raise ValueError(f"Location {location} not found in valid locations")
        except ValueError as e:
            logger.error(f"Error getting location index: {e}")
            return None

    def simulate_cancel_assignment_error(self):
        """Simulate a cancel assignment error with safe fallback"""
        try:
            # Choose a random reason with safe fallback
            reason = random.randint(1, 6)
            reason_text = self.cancel_reasons.get(reason, "Unknown reason")
            
            # Safely access variables
            status_var = self.variables.get("iStatus")
            if status_var:
                status_var.set_value(550)
            
            cancel_var = self.variables.get("iCancelAssignment")
            if cancel_var:
                cancel_var.set_value(reason)
            
            alarm_msg = self.variables.get("sAlarmMessage")
            if alarm_msg:
                alarm_msg.set_value(f"Automaat: Cancel Assignment Error ({reason})")
            
            short_alarm = self.variables.get("sShortAlarmDescription")
            if short_alarm:
                short_alarm.set_value(f"CANCEL_ASSIGNMENT_{reason}")
            
            alarm_solution = self.variables.get("sAlarmSolution")
            if alarm_solution:
                alarm_solution.set_value(reason_text)
            
            # Force UI update
            self.message_queue.put(("update", {"variable_name": "iStatus", "value": 550}))
            self.message_queue.put(("update", {"variable_name": "sAlarmMessage", "value": f"Automaat: Cancel Assignment Error ({reason})"}))
            self.message_queue.put(("update", {"variable_name": "sShortAlarmDescription", "value": f"CANCEL_ASSIGNMENT_{reason}"}))
            self.message_queue.put(("update", {"variable_name": "sAlarmSolution", "value": reason_text}))
            
            self.show_error("Automaat Error", f"Cancel Assignment ({reason}): {reason_text}\nDe automaat is gepauzeerd. Reset handmatig om verder te gaan.")
            self.stop_automaat_mode()
            
        except Exception as e:
            logger.error(f"Error in simulate_cancel_assignment_error: {e}")
            self.show_error("Error", f"Failed to simulate error: {e}")

    def start_job(self):
        """Start a job with input validation"""
        try:
            job_type = self.job_type.get()
            try:
                origin = int(self.origin_var.get())
                destination = int(self.destination_var.get())
            except ValueError:
                self.show_error("Invalid Input", "Origin and destination must be valid numbers")
                return
            
            if not self.validate_job_inputs(job_type, origin, destination):
                return
            
            # Create job data
            task_type_map = {
                "Full Placement": 1,
                "Move To": 2,
                "Bring Away": 4
            }
            self._pending_job = {
                'job_type': job_type,
                'origin': origin,
                'destination': destination,
                'task_type': task_type_map.get(job_type, 1)
            }
            
            # Update status to BUSY
            try:
                if "iStatus" in self.variables:
                    self.variables["iStatus"].set_value(1)  # BUSY
                if "iTaskType" in self.variables:
                    self.variables["iTaskType"].set_value(task_type_map.get(job_type, 1))
                if "iMainStatus" in self.variables:
                    self.variables["iMainStatus"].set_value(1)  # SYSTEM_RUNNING
                if "iOrigin" in self.variables:
                    self.variables["iOrigin"].set_value(origin)
                if "iDestination" in self.variables:
                    self.variables["iDestination"].set_value(destination)
                
                # Log job start
                if "sLastJob" in self.variables:
                    self.variables["sLastJob"].set_value(f"Starting {job_type}: {origin} -> {destination}")
                
                # Force UI update
                self.message_queue.put(("update", {"variable_name": "iStatus", "value": 1}))
                self.message_queue.put(("update", {"variable_name": "iTaskType", "value": task_type_map.get(job_type, 1)}))
                self.message_queue.put(("update", {"variable_name": "iMainStatus", "value": 1}))
                self.message_queue.put(("update", {"variable_name": "iOrigin", "value": origin}))
                self.message_queue.put(("update", {"variable_name": "iDestination", "value": destination}))
                
                logger.info(f"Started job: {job_type} from {origin} to {destination}")
            except Exception as e:
                logger.error(f"Error updating PLC variables: {e}")
                self.show_error("Error", f"Failed to update PLC status: {e}")
                return
            
            # Start animation
            self.root.after(0, lambda: self.animate_simple_lift(origin, destination, job_type))
            
        except Exception as e:
            logger.error(f"Error starting job: {e}")
            self.show_error("Error", f"Failed to start job: {e}")

    def cancel_job(self):
        try:
            self.variables["iStatus"].set_value(0)  # IDLE
            self.variables["iTaskType"].set_value(0)  # No task
            
            # Update lift state
            self.lift_moving = False
            self.draw_simple_lift(250, 'mid', False)
            
            logger.info("Job cancelled")
        except Exception as e:
            logger.error(f"Failed to cancel job: {e}")
            messagebox.showerror("Error", f"Failed to cancel job: {e}")

    def send_error(self):
        try:
            error_code = 888  # STATION_ERROR
            error_text = "Test Error Message"
            solution = "Test Error Solution"
            
            self.variables["iStatus"].set_value(2)  # ERROR
            self.variables["iError"].set_value(error_code)
            self.variables["iErrorCode"].set_value(error_code)
            self.variables["iErrorText"].set_value(error_text)
            self.variables["sShortAlarmDescription"].set_value("TEST_ERR")
            self.variables["sAlarmMessage"].set_value(error_text)
            self.variables["sAlarmSolution"].set_value(solution)
            
            # Log error
            if "sErrorHistory" in self.variables:
                current_history = self.variables["sErrorHistory"].get_value()
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                new_entry = f"[{timestamp}] Error {error_code}: {error_text}"
                new_history = f"{new_entry}\n{current_history}"
                if new_history.count('\n') > 10:  # Keep only last 10 entries
                    new_history = '\n'.join(new_history.split('\n')[:10])
                self.variables["sErrorHistory"].set_value(new_history)
            if "iTotalErrors" in self.variables:
                total_errors = self.variables["iTotalErrors"].get_value()
                self.variables["iTotalErrors"].set_value(total_errors + 1)
            
            # Log to file
            self.log_error_to_file(error_code, error_text)
            
            logger.info("Error message sent")
        except Exception as e:
            logger.error(f"Failed to send error: {e}")
            messagebox.showerror("Error", f"Failed to send error: {e}")

    def run(self):
        """Start the application"""
        def check_queue():
            """Check message queue for updates and errors"""
            try:
                while True:
                    msg_type, msg = self.message_queue.get_nowait()
                    if msg_type == "error":
                        messagebox.showerror("Error", msg)
                    elif msg_type == "update":
                        self.update_ui(msg)
                    elif msg_type == "connection":
                        self.connection_label.config(text=f"Status: {msg}", foreground="green" if msg == "Connected" else "red")
                        self.set_controls_state("normal" if msg == "Connected" else "disabled")
            except queue.Empty:
                pass
            self.root.after(100, check_queue)

        def ping_connection():
            """Check connection status and refresh all values"""
            try:
                if "xWatchDog" in self.variables:
                    # Update watchdog
                    current_watchdog = self.variables["xWatchDog"].get_value()
                    self.variables["xWatchDog"].set_value(not current_watchdog)
                    
                    # Force refresh of all status values
                    self.read_all_values()
                    
                    # Update connection status
                    self.connection_label.config(text="Status: Connected", foreground="green")
                    self.set_controls_state("normal")
                else:
                    logger.warning("[PING] Watchdog variable not found")
                    self.connection_label.config(text="Status: Disconnected", foreground="red")
                    self.set_controls_state("disabled")
            except Exception as e:
                logger.error(f"[PING] Connection check failed: {e}")
                self.connection_label.config(text="Status: Disconnected", foreground="red")
                self.set_controls_state("disabled")
            self.root.after(1000, ping_connection)

        try:
            # Start the queue checker
            logger.info("[RUN] Starting message queue checker")
            self.root.after(100, check_queue)
            
            # Start the connection pinger
            logger.info("[RUN] Starting connection pinger")
            self.root.after(1000, ping_connection)
            
            # Start the Tkinter main loop
            logger.info("[RUN] Starting Tkinter main loop")
            self.root.mainloop()
            
        except Exception as e:
            logger.error(f"[RUN] Error starting application: {e}")
            messagebox.showerror("Error", f"Failed to start application: {e}")

    def stop_plc_sim(self):
        """Stop the PLC simulator by setting the stop flag"""
        try:
            if self.client and "xStopServer" in self.variables:
                logger.info("[UI] Attempting to stop PLC Simulator...")
                self.variables["xStopServer"].set_value(True)
                logger.info("[UI] xStopServer=True sent to PLC Sim")
                messagebox.showinfo("PLC Simulator", "Stop command sent to PLC Simulator")
            else:
                logger.error("[UI] Cannot stop PLC Simulator - client not connected or xStopServer not found")
                messagebox.showerror("Error", "Cannot stop PLC Simulator - not connected")
        except Exception as e:
            logger.error(f"[UI] Error stopping PLC Simulator: {e}")
            messagebox.showerror("Error", f"Failed to stop PLC Simulator: {e}")

    def animate_simple_lift(self, origin, destination, job_type=None):
        logger.info(f"[ANIM] Start animate_simple_lift: origin={origin}, destination={destination}, job_type={job_type}")
        even_locs = list(range(100, -3, -2))
        odd_locs = list(range(99, -2, -2))
        origin_side = 'left' if origin % 2 == 0 else 'right'
        dest_side = 'left' if destination % 2 == 0 else 'right'
        if origin_side == 'left':
            origin_idx = even_locs.index(origin)
        else:
            origin_idx = odd_locs.index(origin)
        if dest_side == 'left':
            dest_idx = even_locs.index(destination)
        else:
            dest_idx = odd_locs.index(destination)
        logger.info(f"[ANIM] origin_idx={origin_idx}, dest_idx={dest_idx}, origin_side={origin_side}, dest_side={dest_side}")
        self._pending_anim = {
            'origin_side': origin_side,
            'origin_idx': origin_idx,
            'dest_idx': dest_idx,
            'dest_side': dest_side,
            'job_type': job_type
        }
        if not hasattr(self, 'lift_loc_idx'):
            self.lift_loc_idx = origin_idx
        logger.info(f"[ANIM] Current lift_loc_idx={self.lift_loc_idx}, moving to origin_idx={origin_idx}")
        self._move_lift_index(self.lift_loc_idx, origin_idx, self._after_move_to_origin)

    def _after_move_to_origin(self):
        anim = getattr(self, '_pending_anim', None)
        logger.info(f"[ANIM] _after_move_to_origin called, anim={anim}")
        if not anim:
            logger.warning("[ANIM] _after_move_to_origin: No pending anim!")
            return
        self.lift_loc_idx = anim['origin_idx']
        self.lift_side = 'mid'
        self.tray_on_lift = False
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        jt = anim['job_type'] if anim['job_type'] is not None else self.job_type.get()
        logger.info(f"[ANIM] _after_move_to_origin: job_type={jt}")
        if jt == "Full Placement":
            logger.info(f"[ANIM] Start Full Placement anim flow")
            self.root.after(10, lambda: self._fp_move_side(anim['origin_side'], anim['origin_idx'], anim['dest_idx'], anim['dest_side']))
        elif jt == "Move To":
            logger.info(f"[ANIM] Start Move To anim flow")
            self.root.after(10, lambda: self._move_to_anim(anim['dest_idx']))
        elif jt == "Bring Away":
            logger.info(f"[ANIM] Start Bring Away anim flow")
            self.tray_on_lift = True
            self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
            self.root.after(300, lambda: self._bring_away_anim(anim['dest_idx'], anim['dest_side']))
        else:
            logger.info(f"[ANIM] Unknown job type, fallback to Full Placement")
            self.root.after(10, lambda: self._fp_move_side(anim['origin_side'], anim['origin_idx'], anim['dest_idx'], anim['dest_side']))

    def _move_lift_index(self, from_idx, to_idx, callback):
        logger.info(f"[ANIM] _move_lift_index: from_idx={from_idx}, to_idx={to_idx}")
        if from_idx == to_idx:
            self.lift_loc_idx = to_idx
            self.draw_simple_lift(self.lift_loc_idx, getattr(self, 'lift_side', 'mid'), getattr(self, 'tray_on_lift', False))
            logger.info(f"[ANIM] _move_lift_index: Arrived at {to_idx}, calling callback")
            self.root.after(300, callback)
            return
        
        # Calculate step size based on direction
        step = 1 if to_idx > from_idx else -1
        self.lift_loc_idx = from_idx + step
        self.draw_simple_lift(self.lift_loc_idx, getattr(self, 'lift_side', 'mid'), getattr(self, 'tray_on_lift', False))
        
        # Use consistent delay regardless of tray presence
        self.root.after(100, lambda: self._move_lift_index(self.lift_loc_idx, to_idx, callback))

    def _fp_move_side(self, side, origin_idx, dest_idx, dest_side):
        logger.info(f"[ANIM] _fp_move_side: side={side}, origin_idx={origin_idx}, dest_idx={dest_idx}, dest_side={dest_side}")
        self.lift_side = side
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        self.root.after(300, lambda: self._fp_pickup(origin_idx, dest_idx, dest_side))

    def _fp_pickup(self, origin_idx, dest_idx, dest_side):
        logger.info(f"[ANIM] _fp_pickup: origin_idx={origin_idx}, dest_idx={dest_idx}, dest_side={dest_side}")
        self.tray_on_lift = True
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        self.root.after(300, lambda: self._fp_return_mid(origin_idx, dest_idx, dest_side))

    def _fp_return_mid(self, origin_idx, dest_idx, dest_side):
        logger.info(f"[ANIM] _fp_return_mid: origin_idx={origin_idx}, dest_idx={dest_idx}, dest_side={dest_side}")
        self.lift_side = 'mid'
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        self.root.after(300, lambda: self._fp_move_vert_dest(dest_idx, dest_side))

    def _fp_move_vert_dest(self, dest_idx, dest_side):
        logger.info(f"[ANIM] _fp_move_vert_dest: dest_idx={dest_idx}, current={self.lift_loc_idx}, dest_side={dest_side}")
        step = 1 if dest_idx > self.lift_loc_idx else -1
        if self.lift_loc_idx == dest_idx:
            self.draw_simple_lift(self.lift_loc_idx, 'mid', True)
            logger.info(f"[ANIM] _fp_move_vert_dest: Arrived at dest_idx={dest_idx}, moving to side {dest_side}")
            self.root.after(200, lambda: self._fp_move_side_dest(dest_side, dest_idx))
            return
        self.lift_loc_idx += step
        self.draw_simple_lift(self.lift_loc_idx, 'mid', True)
        self.root.after(100, lambda: self._fp_move_vert_dest(dest_idx, dest_side))

    def _fp_move_side_dest(self, dest_side, dest_idx):
        logger.info(f"[ANIM] _fp_move_side_dest: dest_side={dest_side}, dest_idx={dest_idx}")
        self.lift_side = dest_side
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        self.root.after(300, self._fp_dropoff)

    def _fp_dropoff(self):
        logger.info(f"[ANIM] _fp_dropoff: lift_loc_idx={self.lift_loc_idx}, lift_side={self.lift_side}")
        self.tray_on_lift = False
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        self.root.after(300, self._simple_lift_finish)

    def _simple_lift_finish(self):
        """Handle lift animation completion with proper synchronization"""
        with self._animation_lock:
            logger.info(f"[ANIM] _simple_lift_finish: lift_loc_idx={self.lift_loc_idx}, lift_side={self.lift_side}")
            self.lift_side = 'mid'
            self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
            
            if self.mode.get() == "Automaat" and getattr(self, '_automaat_waiting', False):
                self._automaat_waiting = False
                if random.random() < self.error_simulation_chance:
                    logger.info(f"[ANIM] _simple_lift_finish: Simulating cancel assignment error")
                    self.simulate_cancel_assignment_error()
                    return
                logger.info(f"[ANIM] _simple_lift_finish: Starting next automaat job")
                self.root.after(1000, self._automaat_loop)
            
            if hasattr(self, '_pending_job'):
                job = self._pending_job
                del self._pending_job
                logger.info(f"[ANIM] _simple_lift_finish: Updating PLC status for job {job}")
                self._finish_job_plc(job)
                
                # Force UI update after job completion
                self.root.after(100, self.read_all_values)

    def _bring_away_anim(self, dest_idx, dest_side):
        logger.info(f"[ANIM] _bring_away_anim: dest_idx={dest_idx}, dest_side={dest_side}")
        # Verticaal naar destination
        step = 1 if dest_idx > self.lift_loc_idx else -1
        if self.lift_loc_idx == dest_idx:
            self.draw_simple_lift(self.lift_loc_idx, 'mid', True)
            logger.info(f"[ANIM] _bring_away_anim: Arrived at dest_idx={dest_idx}, moving to side {dest_side}")
            self.root.after(200, lambda: self._fp_move_side_dest(dest_side, dest_idx))
            return
        self.lift_loc_idx += step
        self.draw_simple_lift(self.lift_loc_idx, 'mid', True)
        self.root.after(100, lambda: self._bring_away_anim(dest_idx, dest_side))

    def _move_to_anim(self, dest_idx):
        logger.info(f"[ANIM] _move_to_anim: dest_idx={dest_idx}")
        step = 1 if dest_idx > self.lift_loc_idx else -1
        if self.lift_loc_idx == dest_idx:
            # Bepaal zijde op basis van even/oneven
            even_locs = list(range(100, -3, -2))
            odd_locs = list(range(99, -2, -2))
            dest_val = even_locs[dest_idx] if dest_idx < len(even_locs) else -2
            lift_side = 'left' if dest_val % 2 == 0 else 'right'
            self.lift_side = lift_side
            self.draw_simple_lift(self.lift_loc_idx, self.lift_side, False)
            logger.info(f"[ANIM] _move_to_anim: Arrived at dest_idx={dest_idx}, finishing on side {lift_side}")
            self.root.after(300, self._simple_lift_finish)
            return
        self.lift_loc_idx += step
        self.draw_simple_lift(self.lift_loc_idx, 'mid', False)
        self.root.after(100, lambda: self._move_to_anim(dest_idx))

    def _finish_job_plc(self, job):
        try:
            # Update PLC status based on job type
            if "iMainStatus" in self.variables:
                self.variables["iMainStatus"].set_value(1)  # SYSTEM_RUNNING
            if "iStatus" in self.variables:
                self.variables["iStatus"].set_value(3)  # COMPLETED
            if "iTaskType" in self.variables:
                self.variables["iTaskType"].set_value(job['task_type'])
            if "iOrigin" in self.variables:
                self.variables["iOrigin"].set_value(job['origin'])
            if "iDestination" in self.variables:
                self.variables["iDestination"].set_value(job['destination'])
            
            # Log job completion
            if "sLastJob" in self.variables:
                self.variables["sLastJob"].set_value(f"Completed {job['job_type']}: {job['origin']} -> {job['destination']}")
            if "sJobHistory" in self.variables:
                current_history = self.variables["sJobHistory"].get_value()
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                new_entry = f"[{timestamp}] {job['job_type']}: {job['origin']} -> {job['destination']} (Completed)"
                new_history = f"{new_entry}\n{current_history}"
                if new_history.count('\n') > 10:  # Keep only last 10 entries
                    new_history = '\n'.join(new_history.split('\n')[:10])
                self.variables["sJobHistory"].set_value(new_history)
            if "iTotalJobs" in self.variables:
                total_jobs = self.variables["iTotalJobs"].get_value()
                self.variables["iTotalJobs"].set_value(total_jobs + 1)
            
            # Log to file
            self.log_job_to_file(job['job_type'], job['origin'], job['destination'], "COMPLETED")
            
            # Force UI update
            self.message_queue.put(("update", {"variable_name": "iMainStatus", "value": 1}))
            self.message_queue.put(("update", {"variable_name": "iStatus", "value": 3}))
            self.message_queue.put(("update", {"variable_name": "iTaskType", "value": job['task_type']}))
            self.message_queue.put(("update", {"variable_name": "iOrigin", "value": job['origin']}))
            self.message_queue.put(("update", {"variable_name": "iDestination", "value": job['destination']}))
            
            logger.info(f"[ANIM] Job {job} finished successfully")
        except Exception as e:
            logger.error(f"[ANIM] Error finishing job {job}: {e}")

    def on_mode_change(self, event=None):
        if self.mode.get() == "Automaat":
            self.start_automaat_mode()
        else:
            self.stop_automaat_mode()

    def start_automaat_mode(self):
        self.automaat_running = True
        self._automaat_loop()

    def stop_automaat_mode(self):
        self.automaat_running = False

    def _automaat_loop(self):
        if not getattr(self, 'automaat_running', False):
            return
        even_locs = list(range(100, -3, -2))
        odd_locs = list(range(99, -2, -2))
        all_locs = even_locs + odd_locs
        origin = random.choice(all_locs)
        destination = random.choice([loc for loc in all_locs if loc != origin])
        self.origin_var.set(str(origin))
        self.destination_var.set(str(destination))
        self.job_type.set("Full Placement")
        self._automaat_waiting = True
        self.start_job()
        # De volgende job wordt pas gestart na _simple_lift_finish

    def reset_lift(self):
        self.stop_automaat_mode()
        even_locs = list(range(100, -3, -2))
        home_idx = even_locs.index(-2)
        self.lift_loc_idx = home_idx
        self.lift_side = 'mid'
        self.tray_on_lift = False
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        logger.info("[RESET] Lift reset naar midden op positie -2, automaat gestopt.")

if __name__ == "__main__":
    simulator = EcoSystemSimulator()
    simulator.run()
