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
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Set specific loggers to higher levels to reduce noise
logging.getLogger('opcua').setLevel(logging.WARNING)
logging.getLogger('opcua.client').setLevel(logging.WARNING)
logging.getLogger('opcua.client.ua_client').setLevel(logging.WARNING)
logging.getLogger('opcua.client.ua_client.Socket').setLevel(logging.WARNING)
logging.getLogger('opcua.uaprotocol').setLevel(logging.WARNING)
logging.getLogger('opcua.common.subscription').setLevel(logging.WARNING)

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
                logger.info(f"[HANDLER] Data change notification: {var_name} = {val}")
                self.message_queue.put(("update", {"variable_name": var_name, "value": val}))
            else:
                # Fallback - use the node ID string
                logger.info(f"[HANDLER] Data change notification (fallback): {node_id_str} = {val}")
                self.message_queue.put(("update", {node_id_str: val}))
                
        except Exception as e:
            logger.error(f"[HANDLER] Error in datachange handler: {e}")

class Model:
    """Model class that holds the application state"""
    def __init__(self):
        self.lift_position = -2
        self.lift_side = 'mid'
        self.tray_on_lift = False
        self.current_values = {
            "iMainStatus": 0,
            "iStatus": 0,
            "iTaskType": 0,
            "iStationStatus": 0,
            "xWatchDog": False,
            "xTrayInElevator": False
        }
        self.is_connected = False
        self.animation_running = False
        self.system_mode_map = {
            0: "MANUAL",
            1: "AUTOMATIC", 
            2: "MAINTENANCE"
        }
        self.task_type_map = {
            0: "NONE",
            1: "FULL_PLACEMENT",
            2: "MOVE_TO",
            4: "BRING_AWAY"
        }
        self.status_map = {
            0: "IDLE",
            1: "BUSY",
            2: "ERROR",
            3: "COMPLETED",
            20: "WAIT_ECOSYSTEM",
            550: "CANCELLED"
        }
        
        # Observers for UI updates
        self.observers = []

    def add_observer(self, observer):
        """Add an observer to be notified of model changes"""
        self.observers.append(observer)

    def notify_observers(self, var_name, value):
        """Notify all observers of a model change"""
        for observer in self.observers:
            observer.model_updated(var_name, value)

    def update_value(self, var_name, value):
        """Update a value and notify observers"""
        if var_name in self.current_values:
            self.current_values[var_name] = value
            self.notify_observers(var_name, value)

class View:
    """View class responsible for rendering"""
    def __init__(self, root, model):
        self.root = root
        self.model = model
        self._last_render_time = 0
        self._min_render_interval = 16  # ~60 FPS
        self.needs_redraw = True
        self.mode = tk.StringVar(value="Handmatig")
        self._last_dimensions = None
        self._last_draw_time = 0
        self._min_draw_interval = 16
        self._canvas_items = {}
        self._resize_timer = None
        self._queued_resize = False
        self._animation_lock = threading.Lock()
        self._animation_running = False
        self.is_connected = False
        self.automaat_running = False
        
        # Job validation
        self.valid_job_types = {
            "Reset": 0,
            "Full Placement": 1,
            "Move To": 2,
            "Prepare Operation": 3,
            "Bring Away": 4
        }
        self.all_locs = list(range(100, -3, -2)) + list(range(99, -2, -2))  # Even and odd locations
        self.even_locs = list(range(100, -3, -2))
        self.odd_locs = list(range(99, -2, -2))
        
        # OPC UA client settings
        self.client = None
        self.variables = {}
        self.nodeid_map = {}
        self.message_queue = queue.Queue()
        self.server_url = "opc.tcp://127.0.0.1:4860"
        self.connection_timeout = 5000
        self.keepalive_interval = 10000
        self.keepalive_count = 3
        
        # Register as observer
        self.model.add_observer(self)
        
        # Setup UI elements
        self.setup_ui()
        
        # Start render loop
        self.start_render_loop()
    
    def setup_ui(self):
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
        self.job_type = ttk.Combobox(control_frame, values=list(self.valid_job_types.keys()), state="readonly")
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
        self.reconnect_button = ttk.Button(button_frame, text="Reconnect", command=self.reconnect_to_plc)
        self.reconnect_button.pack(side="left", padx=5)

        # Connection status
        self.connection_label = ttk.Label(left_frame, text="Status: Disconnected", foreground="red")
        self.connection_label.pack(pady=5)
        
        # Lift Visualization
        lift_frame = ttk.LabelFrame(right_frame, text="Lift Visualization", padding="5")
        lift_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Canvas setup with resize handling
        self.canvas = Canvas(lift_frame, width=300, height=900, bg="lightgray")
        self.canvas.pack(padx=10, pady=10, fill="both", expand=True)
        self.canvas.bind("<Configure>", self._handle_resize)
        
        # Initial lift drawing
        self.draw_simple_lift(250, 'mid', False)
        
        # Disable all controls initially
        self.set_controls_state("disabled")

    def set_controls_state(self, is_connected):
        """Enable or disable all controls based on connection state"""
        try:
            state = "normal" if is_connected else "disabled"
            logger.info(f"[VIEW] Setting controls state to {state}")
            
            # Update connection status
            self.is_connected = is_connected
            self.connection_label.config(
                text=f"Status: {'Connected' if is_connected else 'Disconnected'}", 
                foreground="green" if is_connected else "red"
            )
            
            # Update comboboxes
            self.mode_select.configure(state="readonly" if is_connected else "disabled")
            self.job_type.configure(state="readonly" if is_connected else "disabled")
            
            # Update entry fields
            self.origin_entry.configure(state=state)
            self.destination_entry.configure(state=state)
            
            # Update buttons
            self.start_button.configure(state=state)
            self.cancel_button.configure(state=state)
            self.error_button.configure(state=state)
            self.refresh_button.configure(state=state)
            self.reset_button.configure(state=state)
            self.stop_button.configure(state=state)
            
            # Reconnect button is enabled when disconnected
            self.reconnect_button.configure(state="normal" if not is_connected else "disabled")
            
            # Clear visualization if disconnected
            if not is_connected:
                self.canvas.delete("all")
                self._canvas_items.clear()
                self._last_dimensions = None
                self.automaat_running = False
                self._automaat_waiting = False
            
            logger.info(f"[VIEW] Controls state updated to {state}")
            
        except Exception as e:
            logger.error(f"[VIEW] Error setting controls state: {e}")

    def draw_simple_lift(self, loc_idx, lift_side, tray_on_lift, tray_y=None):
        """Draw the lift visualization with performance optimizations"""
        try:
            # Skip drawing if not connected
            if not self.is_connected:
                if hasattr(self, '_canvas_items') and self._canvas_items:
                    self.canvas.delete("all")
                    self._canvas_items.clear()
                return

            # Get current dimensions
            canvas_height = self.canvas.winfo_height()
            canvas_width = self.canvas.winfo_width()
            current_dims = (canvas_width, canvas_height, loc_idx, lift_side, tray_on_lift)
            
            # Skip redraw if dimensions haven't changed and it's too soon
            current_time = time.time() * 1000
            if (self._last_dimensions == current_dims and 
                current_time - self._last_draw_time < self._min_draw_interval):
                return
                
            self._last_draw_time = current_time
            self._last_dimensions = current_dims
            
            # Calculate dimensions
            left_x = max(20, int(canvas_width * 0.1))
            mid_x = max(120, int(canvas_width * 0.4))
            right_x = max(220, int(canvas_width * 0.7))
            schacht_top = 30
            
            # Calculate block height
            even_locs = list(range(100, -3, -2))
            block_h = max(10, int((canvas_height - 60) / len(even_locs)))
            
            # Only clear and redraw if dimensions changed
            if not self._canvas_items or self._canvas_items.get('dims') != current_dims:
                self.canvas.delete("all")
                self._canvas_items.clear()
                self._canvas_items['dims'] = current_dims
                
                # Draw static elements (locations and shaft)
                self._draw_static_elements(left_x, mid_x, right_x, schacht_top, block_h, even_locs)
            
            # Always update dynamic elements (lift and tray)
            self._draw_dynamic_elements(loc_idx, lift_side, tray_on_lift, tray_y,
                                     left_x, mid_x, right_x, schacht_top, block_h)
            
        except Exception as e:
            logger.error(f"Error in draw_simple_lift: {e}")

    def _draw_static_elements(self, left_x, mid_x, right_x, schacht_top, block_h, even_locs):
        """Draw static elements of the visualization (locations and shaft)"""
        try:
            # Draw even locations (left)
            for i, loc in enumerate(even_locs):
                y = schacht_top + i * block_h
                self.canvas.create_rectangle(left_x, y, left_x+40, y+block_h-2,
                                          fill="#e0e0e0", outline="gray", tags="static")
                self.canvas.create_text(left_x+20, y+block_h/2, text=str(loc),
                                     font=("Arial", 8), tags="static")
            
            # Draw odd locations (right)
            odd_locs = list(range(99, -2, -2))
            for i, loc in enumerate(odd_locs):
                y = schacht_top + i * block_h
                self.canvas.create_rectangle(right_x, y, right_x+40, y+block_h-2,
                                          fill="#e0e0e0", outline="gray", tags="static")
                self.canvas.create_text(right_x+20, y+block_h/2, text=str(loc),
                                     font=("Arial", 8), tags="static")
            
            # Draw lift shaft
            self.canvas.create_rectangle(mid_x, schacht_top, mid_x+40,
                                      schacht_top+len(even_locs)*block_h,
                                      outline="blue", width=2, tags="static")
        except Exception as e:
            logger.error(f"Error drawing static elements: {e}")

    def _draw_dynamic_elements(self, loc_idx, lift_side, tray_on_lift, tray_y,
                             left_x, mid_x, right_x, schacht_top, block_h):
        """Draw dynamic elements of the visualization (lift and tray)"""
        try:
            # Clear previous dynamic elements
            self.canvas.delete("dynamic")
            
            # Draw lift
            y = schacht_top + loc_idx * block_h
            lift_x = mid_x if lift_side == 'mid' else (left_x if lift_side == 'left' else right_x)
            self.canvas.create_rectangle(lift_x, y, lift_x+40, y+block_h-2,
                                      fill="green", outline="black", width=2,
                                      tags="dynamic")
            
            # Draw tray if present
            if tray_on_lift:
                tray_x = lift_x+5
                tray_y = y+3 if tray_y is None else tray_y
                self.canvas.create_rectangle(tray_x, tray_y, tray_x+30, tray_y+block_h-8,
                                          fill="orange", outline="black", width=2,
                                          tags="dynamic")
        except Exception as e:
            logger.error(f"Error drawing dynamic elements: {e}")

    def _handle_resize(self, event=None):
        """Handle window resize events with debouncing"""
        if self._resize_timer is not None:
            self.root.after_cancel(self._resize_timer)
        
        # Force redraw after resize
        self._last_dimensions = None
        self._resize_timer = self.root.after(100, self._delayed_resize)

    def _delayed_resize(self):
        """Perform the actual resize operation after debouncing"""
        try:
            self._resize_timer = None
            if hasattr(self, 'lift_loc_idx'):
                self.draw_simple_lift(self.lift_loc_idx,
                                    getattr(self, 'lift_side', 'mid'),
                                    getattr(self, 'tray_on_lift', False))
        except Exception as e:
            logger.error(f"Error in delayed resize: {e}")

    def connect_to_plc(self):
        """Connect to the PLC simulator"""
        try:
            logger.info("[CONN] Creating client...")
            self.client = Client(url=self.server_url)
            self.client.timeout = self.connection_timeout
            self.client.session_keepalive = self.keepalive_interval
            self.client.session_keepalive_count = self.keepalive_count
            
            logger.info("[CONN] Client settings configured")
            logger.info(f"[CONN] Connecting to {self.server_url}")
            
            # Connect to server
            self.client.connect()
            logger.info("[CONN] Connected successfully")
            self.is_connected = True
            self.set_controls_state(True)
            
            # Get namespace index
            uri = "http://plcsim.example.com"
            logger.info(f"[CONN] Getting namespace index for {uri}")
            self.namespace = self.client.get_namespace_index(uri)
            logger.info(f"[CONN] Found namespace index: {self.namespace}")
            
            # Get Objects node
            logger.info("[CONN] Getting Objects node")
            objects = self.client.get_objects_node()
            logger.info(f"[CONN] Objects node: {objects}")
            
            # Get PLC node
            logger.info("[CONN] Getting PLC node")
            plc_node = objects.get_child([f"{self.namespace}:PLC"])
            logger.info(f"[CONN] Found PLC node: {plc_node}")
            
            # Get all variables
            var_names = [
                "iMainStatus", "xWatchDog", "iStatus", "iTaskType",
                "iOrigin", "iDestination", "iError", "iErrorCode",
                "iErrorText", "iMode", "sShortAlarmDescription",
                "sAlarmMessage", "sAlarmSolution", "iStationStatus",
                "xTrayInElevator", "xAcknowledgeMovement", "iCancelReason"
            ]
            
            logger.info("[CONN] Getting variable nodes...")
            for var_name in var_names:
                try:
                    node = plc_node.get_child([f"{self.namespace}:{var_name}"])
                    self.variables[var_name] = node
                    node_id_str = str(node.nodeid)
                    self.nodeid_map[node_id_str] = var_name
                    logger.info(f"[CONN] Found variable: {var_name} with node ID: {node_id_str}")
                except Exception as var_error:
                    logger.error(f"[CONN] Error getting variable {var_name}: {var_error}")
            
            # Read initial values
            self.read_all_values()
            
            # Start subscription
            logger.info("[CONN] Creating subscription handler")
            handler = DataChangeHandler(self.message_queue, self.nodeid_map)
            
            logger.info("[CONN] Creating subscription")
            subscription = self.client.create_subscription(100, handler)  # Faster update rate
            
            # Subscribe to each variable
            logger.info("[CONN] Subscribing to variables")
            for var_name, var_node in self.variables.items():
                try:
                    handle = subscription.subscribe_data_change(var_node)
                    logger.info(f"[CONN] Subscribed to {var_name} with handle {handle}")
                except Exception as sub_error:
                    logger.error(f"[CONN] Error subscribing to {var_name}: {sub_error}")
            
            logger.info("[CONN] PLC connection and monitoring initialized")
            self.message_queue.put(("connection", "Connected"))
            
        except Exception as e:
            logger.error(f"[CONN] Error connecting to PLC: {e}", exc_info=True)
            self.message_queue.put(("error", f"Connection error: {e}"))
            if self.client:
                try:
                    self.client.disconnect()
                except Exception as disconnect_error:
                    logger.error(f"[CONN] Error during disconnect: {disconnect_error}")
                self.client = None
            self.message_queue.put(("connection", "Disconnected"))

    def read_all_values(self):
        """Read all variable values and update UI"""
        for var_name, node in self.variables.items():
            try:
                value = node.get_value()
                logger.info(f"[READ] Read value for {var_name}: {value}")
                self.message_queue.put(("update", {"variable_name": var_name, "value": value}))
            except Exception as e:
                logger.error(f"[READ] Error reading value for {var_name}: {e}")

    def refresh_values(self):
        """Manually refresh all values from the PLC"""
        self.read_all_values()

    def run_client(self):
        """Run the OPC-UA client in a separate thread"""
        while True:
            try:
                if not self.client or not self.client.uaclient:
                    # Try to connect
                    self.connect_to_plc()
                    time.sleep(1)  # Wait before next connection attempt
                else:
                    # Already connected, wait for a while
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"[CLIENT] Error in client thread: {e}")
                time.sleep(1)  # Wait before retry

    def update_ui(self, data):
        """Update UI with new data"""
        try:
            # Check if data is a dictionary with variable_name and value
            if isinstance(data, dict) and "variable_name" in data and "value" in data:
                var_name = data["variable_name"]
                value = data["value"]
                
                logger.info(f"[UI] Updating {var_name} to {value}")
                
                # Apply value translation for specific variables
                display_value = value
                if var_name == "iMainStatus" and value in self.model.system_mode_map:
                    display_value = f"{value} ({self.model.system_mode_map[value]})"
                elif var_name == "iTaskType" and value in self.model.task_type_map:
                    display_value = f"{value} ({self.model.task_type_map[value]})"
                elif var_name == "iStatus" and value in self.model.status_map:
                    display_value = f"{value} ({self.model.status_map[value]})"
                elif var_name == "xTrayInElevator":
                    display_value = "Yes" if value else "No"
                elif var_name == "xWatchDog":
                    display_value = "Active" if value else "Inactive"
                
                # Update status labels
                if var_name in self.status_labels:
                    # Schedule UI update on main thread
                    self.root.after(0, lambda: self.status_labels[var_name].config(text=str(display_value)))
                    logger.info(f"[UI] Updated status label {var_name} to {display_value}")
                elif var_name in self.alarm_labels:
                    # Schedule UI update on main thread
                    self.root.after(0, lambda: self.alarm_labels[var_name].config(text=str(display_value)))
                    logger.info(f"[UI] Updated alarm label {var_name} to {display_value}")
                
                # If this is a connection-related update, update connection status
                if var_name == "xWatchDog":
                    self.root.after(0, lambda: self.connection_label.config(
                        text="Status: Connected" if value else "Status: Disconnected",
                        foreground="green" if value else "red"
                    ))
                    self.root.after(0, lambda: self.set_controls_state(value))
                
        except Exception as e:
            logger.error(f"[UI] Error updating UI: {e}")

    def validate_job_inputs(self, job_type, origin, destination):
        """Validate job inputs before starting a job"""
        try:
            # Validate job type
            if job_type not in self.valid_job_types:
                raise ValueError(f"Invalid job type: {job_type}")
            
            # Validate origin and destination
            if origin not in self.all_locs:
                raise ValueError(f"Invalid origin location: {origin}")
            if destination not in self.all_locs:
                raise ValueError(f"Invalid destination location: {destination}")
            
            # Additional validation for specific job types
            if job_type == "Full Placement":
                if origin == destination:
                    raise ValueError("Origin and destination cannot be the same for Full Placement")
            elif job_type == "Reset":
                if destination != -2:  # Reset should always go to home position
                    raise ValueError("Reset job must go to home position (-2)")
            
            return True
            
        except ValueError as e:
            logger.error(f"[VIEW] Job validation failed: {e}")
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
            
            cancel_var = self.variables.get("iCancelReason")
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
            # Check connection first
            if not self.is_connected:
                self.show_error("Connection Error", "Not connected to PLC. Please connect first.")
                return

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
            task_type = self.valid_job_types[job_type]
            self._pending_job = {
                'job_type': job_type,
                'origin': origin,
                'destination': destination,
                'task_type': task_type
            }
            
            # Update PLC variables in correct order according to interface document
            try:
                # 1. Set task parameters
                if "iTaskType" in self.variables:
                    self.variables["iTaskType"].set_value(task_type)
                if "iOrigin" in self.variables:
                    self.variables["iOrigin"].set_value(origin)
                if "iDestination" in self.variables:
                    self.variables["iDestination"].set_value(destination)
                
                # 2. Set status to BUSY
                if "iStatus" in self.variables:
                    self.variables["iStatus"].set_value(1)  # BUSY
                if "iMainStatus" in self.variables:
                    self.variables["iMainStatus"].set_value(1)  # SYSTEM_RUNNING
                
                # Log job start
                if "sLastJob" in self.variables:
                    self.variables["sLastJob"].set_value(f"Starting {job_type}: {origin} -> {destination}")
                
                # Force immediate UI update
                self.model.update_value("iTaskType", task_type)
                self.model.update_value("iOrigin", origin)
                self.model.update_value("iDestination", destination)
                self.model.update_value("iStatus", 1)
                self.model.update_value("iMainStatus", 1)
                
                logger.info(f"[JOB] Started {job_type} (type {task_type}) from {origin} to {destination}")
                
                # Start monitoring job status
                self.root.after(100, self._monitor_job_status)
                
            except Exception as e:
                logger.error(f"[JOB] Error updating PLC variables: {e}")
                self.show_error("Error", f"Failed to update PLC status: {e}")
                return
            
        except Exception as e:
            logger.error(f"[JOB] Error starting job: {e}")
            self.show_error("Error", f"Failed to start job: {e}")

    def _monitor_job_status(self):
        """Monitor job status and handle handshakes"""
        try:
            if not self.is_connected or not self._pending_job:
                return

            current_status = self.variables["iStatus"].get_value()
            
            if current_status == 20:  # WAIT_ECOSYSTEM
                # PLC is waiting for acknowledgment
                logger.info("[JOB] PLC waiting for movement acknowledgment")
                self.variables["xAcknowledgeMovement"].set_value(True)
                logger.info("[JOB] Movement acknowledged")
                
            elif current_status == 2:  # ERROR
                # Handle error
                error_code = self.variables["iErrorCode"].get_value()
                error_msg = self.variables["sAlarmMessage"].get_value()
                logger.error(f"[JOB] Job error: {error_code} - {error_msg}")
                self.show_error("Job Error", f"Error {error_code}: {error_msg}")
                self._pending_job = None
                return
                
            elif current_status == 3:  # COMPLETED
                # Job completed
                logger.info("[JOB] Job completed successfully")
                self._pending_job = None
                return
                
            # Continue monitoring
            self.root.after(100, self._monitor_job_status)
            
        except Exception as e:
            logger.error(f"[JOB] Error monitoring job status: {e}")
            self.show_error("Error", f"Failed to monitor job status: {e}")

    def cancel_job(self):
        """Cancel the current job"""
        try:
            if not self.is_connected:
                self.show_error("Connection Error", "Not connected to PLC. Please connect first.")
                return

            # Set cancel reason
            cancel_reason = 1  # Default cancel reason
            if "iCancelReason" in self.variables:
                self.variables["iCancelReason"].set_value(cancel_reason)
            
            # Update status
            if "iStatus" in self.variables:
                self.variables["iStatus"].set_value(550)  # CANCELLED
            
            # Update alarm information
            reason_text = "Job cancelled by user"
            if "sShortAlarmDescription" in self.variables:
                self.variables["sShortAlarmDescription"].set_value(f"CANCEL_ASSIGNMENT_{cancel_reason}")
            if "sAlarmMessage" in self.variables:
                self.variables["sAlarmMessage"].set_value(reason_text)
            if "sAlarmSolution" in self.variables:
                self.variables["sAlarmSolution"].set_value("Reset the system to continue")
            
            # Force immediate UI update
            self.model.update_value("iCancelReason", cancel_reason)
            self.model.update_value("iStatus", 550)
            self.model.update_value("sShortAlarmDescription", f"CANCEL_ASSIGNMENT_{cancel_reason}")
            self.model.update_value("sAlarmMessage", reason_text)
            self.model.update_value("sAlarmSolution", "Reset the system to continue")
            
            # Update lift state
            self.lift_moving = False
            self.draw_simple_lift(250, 'mid', False)
            
            logger.info(f"[JOB] Job cancelled with reason {cancel_reason}: {reason_text}")
            
        except Exception as e:
            logger.error(f"[JOB] Failed to cancel job: {e}")
            self.show_error("Error", f"Failed to cancel job: {e}")

    def send_error(self):
        """Send an error message to the PLC according to interface document"""
        try:
            if not self.is_connected:
                self.show_error("Connection Error", "Not connected to PLC. Please connect first.")
                return

            error_code = 888  # STATION_ERROR
            error_text = "Test Error Message"
            solution = "Test Error Solution"
            
            # Update error variables in correct order
            try:
                # 1. Set error details
                if "iError" in self.variables:
                    self.variables["iError"].set_value(error_code)
                if "iErrorCode" in self.variables:
                    self.variables["iErrorCode"].set_value(error_code)
                if "sShortAlarmDescription" in self.variables:
                    self.variables["sShortAlarmDescription"].set_value("TEST_ERR")
                if "sAlarmMessage" in self.variables:
                    self.variables["sAlarmMessage"].set_value(error_text)
                if "sAlarmSolution" in self.variables:
                    self.variables["sAlarmSolution"].set_value(solution)
                
                # 2. Set status to ERROR
                if "iStatus" in self.variables:
                    self.variables["iStatus"].set_value(2)  # ERROR
                if "iStationStatus" in self.variables:
                    self.variables["iStationStatus"].set_value(error_code)
                
                # Force immediate UI update
                self.model.update_value("iError", error_code)
                self.model.update_value("iErrorCode", error_code)
                self.model.update_value("sShortAlarmDescription", "TEST_ERR")
                self.model.update_value("sAlarmMessage", error_text)
                self.model.update_value("sAlarmSolution", solution)
                self.model.update_value("iStatus", 2)
                self.model.update_value("iStationStatus", error_code)
                
                logger.info(f"[ERROR] Error message sent: {error_code} - {error_text}")
                
                # Stop automaat mode if running
                if self.automaat_running:
                    self.stop_automaat_mode()
                
            except Exception as e:
                logger.error(f"[ERROR] Failed to update error variables: {e}")
                raise
                
        except Exception as e:
            logger.error(f"[ERROR] Failed to send error: {e}")
            self.show_error("Error", f"Failed to send error: {e}")

    def run(self):
        """Start the application"""
        def check_queue():
            """Check message queue for updates and errors"""
            try:
                while True:
                    try:
                        msg_type, msg = self.message_queue.get_nowait()
                        logger.info(f"[QUEUE] Processing message: {msg_type} - {msg}")
                        
                        if msg_type == "error":
                            messagebox.showerror("Error", msg)
                        elif msg_type == "update":
                            if "variable_name" in msg and "value" in msg:
                                self.model.update_value(msg["variable_name"], msg["value"])
                        elif msg_type == "connection":
                            logger.info(f"[QUEUE] Connection status changed to: {msg}")
                            self.model.update_value("xWatchDog", msg == "Connected")
                    except queue.Empty:
                        break
            except Exception as e:
                logger.error(f"[QUEUE] Error in check_queue: {e}")
            finally:
                self.root.after(50, check_queue)

        try:
            # Start the queue checker
            self.root.after(50, check_queue)
            
            # Start the Tkinter main loop
            self.root.mainloop()
            
        except Exception as e:
            logger.error(f"[RUN] Error starting application: {e}")
            messagebox.showerror("Error", f"Failed to start application: {e}")

    def stop_plc_sim(self):
        """Stop the PLC simulator"""
        try:
            if not self.is_connected:
                self.show_error("Error", "Not connected to PLC Simulator")
                return

            logger.info("[UI] Attempting to stop PLC Simulator...")
            self.connection_label.config(text="Status: Stopping PLC...", foreground="orange")
            
            if "xStopServer" in self.variables:
                # Set stop flag
                self.variables["xStopServer"].set_value(True)
                logger.info("[UI] Stop signal sent to PLC Sim")
                
                # Disable controls immediately
                self.set_controls_state(False)
                self.stop_button.configure(state="disabled")
                self.reconnect_button.configure(state="disabled")
                
                # Handle disconnection
                self._handle_server_stopped()
                
                messagebox.showinfo("PLC Simulator", "Stop command sent to PLC Simulator")
            else:
                logger.error("[UI] Cannot stop PLC Simulator - xStopServer variable not found")
                messagebox.showerror("Error", "Cannot stop PLC Simulator - control variable not found")
        except Exception as e:
            logger.error(f"[UI] Error stopping PLC Simulator: {e}")
            messagebox.showerror("Error", f"Failed to stop PLC Simulator: {e}")

    def _check_server_stopped(self):
        """Check if the server has stopped and update UI accordingly"""
        try:
            # Try to read watchdog - if it fails, server has stopped
            if self.client:
                try:
                    self.client.get_node("ns=2;s=PLC.xWatchDog").get_value()
                    # If we get here, server is still running
                    logger.info("[UI] Server still running, checking again in 1 second...")
                    self.root.after(1000, self._check_server_stopped)
                except Exception:
                    logger.info("[UI] Server stopped successfully")
                    self._handle_server_stopped()
            else:
                self._handle_server_stopped()
        except Exception as e:
            logger.error(f"[UI] Error checking server status: {e}")
            self._handle_server_stopped()

    def _handle_server_stopped(self):
        """Handle server stopped state"""
        try:
            if self.client:
                try:
                    self.client.disconnect()
                except:
                    pass
            self.client = None
            self.variables = {}
            self.nodeid_map = {}
            
            # Update UI
            self.is_connected = False
            self.set_controls_state(False)
            self.connection_label.config(text="Status: PLC Stopped", foreground="red")
            self.reconnect_button.configure(state="normal")
            
            logger.info("[UI] Server stopped and disconnected")
            messagebox.showinfo("PLC Simulator", "PLC Simulator has been stopped")
        except Exception as e:
            logger.error(f"[UI] Error handling server stop: {e}")

    def reconnect_to_plc(self):
        """Reconnect to the PLC simulator"""
        try:
            logger.info("[UI] Attempting to reconnect to PLC Simulator...")
            self.connection_label.config(text="Status: Connecting...", foreground="orange")
            
            # Disable reconnect button while attempting to connect
            self.reconnect_button.configure(state="disabled")
            
            # Disconnect existing client if any
            if self.client:
                try:
                    self.client.disconnect()
                except:
                    pass
                self.client = None
                self.variables = {}
                self.nodeid_map = {}
            
            self.is_connected = False
            self.set_controls_state(False)
            
            # Start new client thread
            self.client_thread = threading.Thread(target=self.run_client, daemon=True)
            self.client_thread.start()
            
            logger.info("[UI] Reconnection attempt started")
        except Exception as e:
            logger.error(f"[UI] Error starting reconnection: {e}")
            self.connection_label.config(text="Status: Reconnection Failed", foreground="red")
            self.reconnect_button.configure(state="normal")
            messagebox.showerror("Error", f"Failed to start reconnection: {e}")

    def animate_simple_lift(self, origin, destination, job_type=None):
        """Start lift animation sequence"""
        logger.info(f"[ANIM] Start animate_simple_lift: origin={origin}, destination={destination}, job_type={job_type}")
        
        # Convert locations to indices
        even_locs = list(range(100, -3, -2))
        odd_locs = list(range(99, -2, -2))
        
        # Determine sides
        origin_side = 'left' if origin % 2 == 0 else 'right'
        dest_side = 'left' if destination % 2 == 0 else 'right'
        
        # Get indices
        if origin_side == 'left':
            origin_idx = even_locs.index(origin)
        else:
            origin_idx = odd_locs.index(origin)
            
        if dest_side == 'left':
            dest_idx = even_locs.index(destination)
        else:
            dest_idx = odd_locs.index(destination)
            
        logger.info(f"[ANIM] origin_idx={origin_idx}, dest_idx={dest_idx}, origin_side={origin_side}, dest_side={dest_side}")
        
        # Store animation parameters
        self._pending_anim = {
            'origin_side': origin_side,
            'origin_idx': origin_idx,
            'dest_idx': dest_idx,
            'dest_side': dest_side,
            'job_type': job_type
        }
        
        # Initialize lift position if needed
        if not hasattr(self, 'lift_loc_idx'):
            self.lift_loc_idx = origin_idx
            
        # Start animation sequence
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
        """Move lift vertically to the target index"""
        if from_idx == to_idx:
            self.lift_loc_idx = to_idx
            self.draw_simple_lift(self.lift_loc_idx, getattr(self, 'lift_side', 'mid'), getattr(self, 'tray_on_lift', False))
            logger.debug(f"[ANIM] Lift arrived at position {to_idx}")
            self._animation_running = False
            
            # Handle any queued resize
            if self._queued_resize:
                self._queued_resize = False
                self._handle_resize()
                
            self.root.after(300, callback)
            return
        
        # Calculate step size based on direction
        step = 1 if to_idx > from_idx else -1
        next_idx = from_idx + step
        
        # Update lift position
        self.lift_loc_idx = next_idx
        self.draw_simple_lift(self.lift_loc_idx, getattr(self, 'lift_side', 'mid'), getattr(self, 'tray_on_lift', False))
        
        # Schedule next move
        self.root.after(100, lambda: self._move_lift_index(next_idx, to_idx, callback))

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
        """Move lift vertically to destination"""
        self._animation_running = True
        
        # Check if we've reached the destination
        if self.lift_loc_idx == dest_idx:
            self.draw_simple_lift(self.lift_loc_idx, 'mid', True)
            logger.debug(f"[ANIM] Lift reached destination {dest_idx}")
            self._animation_running = False
            
            # Handle any queued resize
            if self._queued_resize:
                self._queued_resize = False
                self._handle_resize()
                
            self.root.after(200, lambda: self._fp_move_side_dest(dest_side, dest_idx))
            return
        
        # Calculate step size based on direction
        step = 1 if dest_idx > self.lift_loc_idx else -1
        next_idx = self.lift_loc_idx + step
        
        # Update lift position
        self.lift_loc_idx = next_idx
        self.draw_simple_lift(self.lift_loc_idx, 'mid', True)
        
        # Schedule next move
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
            
            self._animation_running = False
            
            # Handle any queued resize
            if self._queued_resize:
                self._queued_resize = False
                self._handle_resize()
            
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
        """Update PLC status after job completion"""
        try:
            # Update PLC variables
            if "iTaskType" in self.variables:
                self.variables["iTaskType"].set_value(job['task_type'])
            if "iOrigin" in self.variables:
                self.variables["iOrigin"].set_value(job['origin'])
            if "iDestination" in self.variables:
                self.variables["iDestination"].set_value(job['destination'])
            if "iStatus" in self.variables:
                self.variables["iStatus"].set_value(3)  # COMPLETED
            if "iMainStatus" in self.variables:
                self.variables["iMainStatus"].set_value(1)  # SYSTEM_RUNNING
            
            # Log job completion
            if "sLastJob" in self.variables:
                self.variables["sLastJob"].set_value(f"Completed {job['job_type']}: {job['origin']} -> {job['destination']}")
            
            # Force immediate UI update
            self.model.update_value("iTaskType", job['task_type'])
            self.model.update_value("iOrigin", job['origin'])
            self.model.update_value("iDestination", job['destination'])
            self.model.update_value("iStatus", 3)  # COMPLETED
            self.model.update_value("iMainStatus", 1)  # SYSTEM_RUNNING
            
            logger.info(f"[JOB] Job {job} finished successfully")
            
            # If in automaat mode, check for next job
            if self.automaat_running:
                self._check_job_completion()
                
        except Exception as e:
            logger.error(f"[JOB] Error finishing job {job}: {e}")

    def on_mode_change(self, event=None):
        if self.mode.get() == "Automaat":
            self.start_automaat_mode()
        else:
            self.stop_automaat_mode()

    def start_automaat_mode(self):
        """Start automaat mode with random job generation"""
        logger.info("[AUTOMAAT] Starting automaat mode")
        self.automaat_running = True
        self._automaat_waiting = False
        self._automaat_loop()

    def stop_automaat_mode(self):
        """Stop automaat mode"""
        logger.info("[AUTOMAAT] Stopping automaat mode")
        self.automaat_running = False
        self._automaat_waiting = False

    def _automaat_loop(self):
        """Generate and execute random jobs in automaat mode"""
        if not self.automaat_running:
            logger.info("[AUTOMAAT] Automaat mode stopped")
            return

        if self._automaat_waiting:
            logger.info("[AUTOMAAT] Waiting for current job to complete")
            return

        # Select random job type (excluding Reset)
        job_types = [jt for jt in self.valid_job_types.keys() if jt != "Reset"]
        job_type = random.choice(job_types)
        
        # Select random locations
        origin = random.choice(self.all_locs)
        destination = random.choice([loc for loc in self.all_locs if loc != origin])
        
        logger.info(f"[AUTOMAAT] Starting new job: {job_type} from {origin} to {destination}")
        
        # Set UI values
        self.job_type.set(job_type)
        self.origin_var.set(str(origin))
        self.destination_var.set(str(destination))
        
        # Start the job
        self._automaat_waiting = True
        self.start_job()
        
        # Schedule next check
        self.root.after(1000, self._check_job_completion)

    def _check_job_completion(self):
        """Check if current job is completed and start next job if needed"""
        if not self.automaat_running:
            return

        # Check current status
        current_status = self.model.current_values.get("iStatus", 0)
        if current_status in [0, 3]:  # IDLE or COMPLETED
            logger.info("[AUTOMAAT] Previous job completed, starting next job")
            self._automaat_waiting = False
            self.root.after(2000, self._automaat_loop)  # Wait 2 seconds before next job
        elif current_status == 2:  # ERROR
            logger.error("[AUTOMAAT] Error detected, stopping automaat mode")
            self.stop_automaat_mode()
        else:
            # Job still running, check again in 1 second
            self.root.after(1000, self._check_job_completion)

    def reset_lift(self):
        self.stop_automaat_mode()
        even_locs = list(range(100, -3, -2))
        home_idx = even_locs.index(-2)
        self.lift_loc_idx = home_idx
        self.lift_side = 'mid'
        self.tray_on_lift = False
        self.draw_simple_lift(self.lift_loc_idx, self.lift_side, self.tray_on_lift)
        logger.info("[RESET] Lift reset naar midden op positie -2, automaat gestopt.")

    def refresh_status_display(self):
        """Refresh all status displays"""
        try:
            # Update all status labels from model
            for var_name, value in self.model.current_values.items():
                self.model_updated(var_name, value)
                
        except Exception as e:
            logger.error(f"Error refreshing status display: {e}")
        finally:
            # Always schedule next refresh
            self.root.after(100, self.refresh_status_display)

    def start_render_loop(self):
        """Start the render loop"""
        def render():
            try:
                current_time = time.time() * 1000
                if current_time - self._last_render_time >= self._min_render_interval:
                    if self.needs_redraw:
                        self.draw_simple_lift(getattr(self, 'lift_loc_idx', 0),
                                           getattr(self, 'lift_side', 'mid'),
                                           getattr(self, 'tray_on_lift', False))
                        self.needs_redraw = False
                    self._last_render_time = current_time
            except Exception as e:
                logger.error(f"Error in render loop: {e}")
            finally:
                self.root.after(16, render)  # Schedule next render
        
        render()  # Start the render loop

    def model_updated(self, var_name, value):
        """Handle updates from the model"""
        try:
            logger.info(f"[VIEW] Model updated: {var_name} = {value}")
            
            # Update status labels
            if var_name in self.status_labels:
                display_value = value
                if var_name == "iMainStatus":
                    display_value = f"{value} ({self.model.system_mode_map.get(value, 'UNKNOWN')})"
                elif var_name == "iStatus":
                    display_value = f"{value} ({self.model.status_map.get(value, 'UNKNOWN')})"
                elif var_name == "iTaskType":
                    display_value = f"{value} ({self.model.task_type_map.get(value, 'UNKNOWN')})"
                    logger.info(f"[VIEW] Task type updated to: {display_value}")
                elif var_name == "xTrayInElevator":
                    display_value = "Yes" if value else "No"
                elif var_name == "xWatchDog":
                    display_value = "Active" if value else "Inactive"
                
                # Update label immediately on main thread
                self.root.after(0, lambda: self.status_labels[var_name].config(text=str(display_value)))
                logger.info(f"[VIEW] Updated status label {var_name} to {display_value}")
            
            # Update alarm labels
            elif var_name in self.alarm_labels:
                # Update label immediately on main thread
                self.root.after(0, lambda: self.alarm_labels[var_name].config(text=str(value)))
                logger.info(f"[VIEW] Updated alarm label {var_name} to {value}")
                
        except Exception as e:
            logger.error(f"[VIEW] Error updating UI: {e}")

class Controller:
    """Controller class that handles user input and business logic"""
    def __init__(self, model, view):
        self.model = model
        self.view = view
        self.client = None
        self.variables = {}
        self.message_queue = queue.Queue()
        
        # Start update loop
        self.start_update_loop()
    
    def start_update_loop(self):
        """Start the update loop"""
        def update():
            try:
                # Process message queue
                while True:
                    try:
                        msg_type, msg = self.message_queue.get_nowait()
                        self.handle_message(msg_type, msg)
                    except queue.Empty:
                        break
                
                # Update model
                self.update_model()
                
                # Mark view for redraw
                self.view.needs_redraw = True
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
            
            # Schedule next update
            self.view.root.after(16, update)  # ~60 FPS
        
        update()
    
    def update_model(self):
        """Update model state"""
        try:
            if self.client and "xWatchDog" in self.variables:
                # Update connection status
                self.model.is_connected = True
                
                # Read current values
                for var_name in self.model.current_values:
                    if var_name in self.variables:
                        try:
                            value = self.variables[var_name].get_value()
                            self.model.current_values[var_name] = value
                        except:
                            pass
            else:
                self.model.is_connected = False
                
        except Exception as e:
            logger.error(f"Error updating model: {e}")
            self.model.is_connected = False
    
    def handle_message(self, msg_type, msg):
        """Handle incoming messages"""
        try:
            if msg_type == "update":
                var_name = msg.get("variable_name")
                value = msg.get("value")
                if var_name in self.model.current_values:
                    self.model.current_values[var_name] = value
            elif msg_type == "connection":
                self.model.is_connected = (msg == "Connected")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

class EcoSystemSimulator:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Ecosystem Simulator")
        self.root.geometry("900x900")
        
        # Create MVC components
        self.model = Model()
        self.view = View(self.root, self.model)
        
        # OPC-UA client setup
        self.client = None
        self.namespace = None
        self.variables = {}
        self.nodeid_map = {}
        self.message_queue = queue.Queue()
        
        # Server connection settings
        self.server_url = "opc.tcp://127.0.0.1:4860"
        self.connection_timeout = 5000
        self.keepalive_interval = 10000
        self.keepalive_count = 3
        
        # Start OPC-UA client in a separate thread
        self.client_thread = threading.Thread(target=self.run_client, daemon=True)
        self.client_thread.start()

    def run(self):
        """Start the application"""
        def check_queue():
            """Check message queue for updates and errors"""
            try:
                while True:
                    try:
                        msg_type, msg = self.message_queue.get_nowait()
                        logger.info(f"[QUEUE] Processing message: {msg_type} - {msg}")
                        
                        if msg_type == "error":
                            messagebox.showerror("Error", msg)
                        elif msg_type == "update":
                            if "variable_name" in msg and "value" in msg:
                                self.model.update_value(msg["variable_name"], msg["value"])
                        elif msg_type == "connection":
                            logger.info(f"[QUEUE] Connection status changed to: {msg}")
                            self.model.update_value("xWatchDog", msg == "Connected")
                    except queue.Empty:
                        break
            except Exception as e:
                logger.error(f"[QUEUE] Error in check_queue: {e}")
            finally:
                self.root.after(50, check_queue)

        try:
            # Start the queue checker
            self.root.after(50, check_queue)
            
            # Start the Tkinter main loop
            self.root.mainloop()
            
        except Exception as e:
            logger.error(f"[RUN] Error starting application: {e}")
            messagebox.showerror("Error", f"Failed to start application: {e}")

    def run_client(self):
        """Run the OPC-UA client in a separate thread"""
        while True:
            try:
                if not self.client or not self.client.uaclient:
                    # Try to connect
                    self.connect_to_plc()
                    time.sleep(1)  # Wait before next connection attempt
                else:
                    # Already connected, wait for a while
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"[CLIENT] Error in client thread: {e}")
                time.sleep(1)  # Wait before retry

    def connect_to_plc(self):
        """Connect to the PLC simulator"""
        try:
            logger.info("[CONN] Creating client...")
            self.client = Client(url=self.server_url)
            self.client.timeout = self.connection_timeout
            self.client.session_keepalive = self.keepalive_interval
            self.client.session_keepalive_count = self.keepalive_count
            
            logger.info("[CONN] Client settings configured")
            logger.info(f"[CONN] Connecting to {self.server_url}")
            
            # Connect to server
            self.client.connect()
            logger.info("[CONN] Connected successfully")
            self.model.is_connected = True
            self.view.set_controls_state(True)
            
            # Get namespace index
            uri = "http://plcsim.example.com"
            logger.info(f"[CONN] Getting namespace index for {uri}")
            self.namespace = self.client.get_namespace_index(uri)
            logger.info(f"[CONN] Found namespace index: {self.namespace}")
            
            # Get Objects node
            logger.info("[CONN] Getting Objects node")
            objects = self.client.get_objects_node()
            logger.info(f"[CONN] Objects node: {objects}")
            
            # Get PLC node
            logger.info("[CONN] Getting PLC node")
            plc_node = objects.get_child([f"{self.namespace}:PLC"])
            logger.info(f"[CONN] Found PLC node: {plc_node}")
            
            # Get all variables
            var_names = [
                "iMainStatus", "xWatchDog", "iStatus", "iTaskType",
                "iOrigin", "iDestination", "iError", "iErrorCode",
                "iErrorText", "iMode", "sShortAlarmDescription",
                "sAlarmMessage", "sAlarmSolution", "iStationStatus",
                "xTrayInElevator", "xAcknowledgeMovement", "iCancelReason"
            ]
            
            logger.info("[CONN] Getting variable nodes...")
            for var_name in var_names:
                try:
                    node = plc_node.get_child([f"{self.namespace}:{var_name}"])
                    self.variables[var_name] = node
                    node_id_str = str(node.nodeid)
                    self.nodeid_map[node_id_str] = var_name
                    logger.info(f"[CONN] Found variable: {var_name} with node ID: {node_id_str}")
                except Exception as var_error:
                    logger.error(f"[CONN] Error getting variable {var_name}: {var_error}")
            
            # Read initial values
            self.read_all_values()
            
            # Start subscription
            logger.info("[CONN] Creating subscription handler")
            handler = DataChangeHandler(self.message_queue, self.nodeid_map)
            
            logger.info("[CONN] Creating subscription")
            subscription = self.client.create_subscription(100, handler)  # Faster update rate
            
            # Subscribe to each variable
            logger.info("[CONN] Subscribing to variables")
            for var_name, var_node in self.variables.items():
                try:
                    handle = subscription.subscribe_data_change(var_node)
                    logger.info(f"[CONN] Subscribed to {var_name} with handle {handle}")
                except Exception as sub_error:
                    logger.error(f"[CONN] Error subscribing to {var_name}: {sub_error}")
            
            logger.info("[CONN] PLC connection and monitoring initialized")
            self.message_queue.put(("connection", "Connected"))
            
        except Exception as e:
            logger.error(f"[CONN] Error connecting to PLC: {e}", exc_info=True)
            self.message_queue.put(("error", f"Connection error: {e}"))
            if self.client:
                try:
                    self.client.disconnect()
                except Exception as disconnect_error:
                    logger.error(f"[CONN] Error during disconnect: {disconnect_error}")
                self.client = None
            self.message_queue.put(("connection", "Disconnected"))

    def read_all_values(self):
        """Read all variable values and update UI"""
        for var_name, node in self.variables.items():
            try:
                value = node.get_value()
                logger.info(f"[READ] Read value for {var_name}: {value}")
                self.message_queue.put(("update", {"variable_name": var_name, "value": value}))
            except Exception as e:
                logger.error(f"[READ] Error reading value for {var_name}: {e}")

if __name__ == "__main__":
    simulator = EcoSystemSimulator()
    simulator.run()
