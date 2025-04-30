import tkinter as tk
from tkinter import ttk, messagebox, Canvas
import asyncio
from asyncua import Client, ua
import threading
import queue
from functools import partial
import logging
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EcoSystemSimulator:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Ecosystem Simulator")
        self.root.geometry("900x700")
        
        # OPC-UA client setup
        self.client = None
        self.namespace_index = None
        self.variables = {}
        self.nodeid_map = {}  # Mapping between nodeId and variable names
        self.message_queue = queue.Queue()
        self.loop = asyncio.new_event_loop()
        
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
        
        # Lift state
        self.current_position = -2
        self.lift_moving = False
        
        # Create UI elements
        self.create_ui()
        
        # Start OPC-UA client in a separate thread
        self.client_thread = threading.Thread(target=self.run_client, daemon=True)
        self.client_thread.start()

    def create_ui(self):
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
        self.job_type = ttk.Combobox(control_frame, values=["Full Placement", "Move To", "Bring Away"])
        self.job_type.grid(row=0, column=1, padx=5, pady=5)
        self.job_type.set("Full Placement")
        
        # Origin and destination inputs
        ttk.Label(control_frame, text="Origin:").grid(row=1, column=0, padx=5, pady=5)
        self.origin_var = tk.StringVar(value="2")
        ttk.Entry(control_frame, textvariable=self.origin_var).grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(control_frame, text="Destination:").grid(row=2, column=0, padx=5, pady=5)
        self.destination_var = tk.StringVar(value="50")
        ttk.Entry(control_frame, textvariable=self.destination_var).grid(row=2, column=1, padx=5, pady=5)
        
        # Buttons
        button_frame = ttk.Frame(control_frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=10)
        
        ttk.Button(button_frame, text="Start Job", command=self.start_job).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Cancel Job", command=self.cancel_job).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Send Error", command=self.send_error).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Refresh Values", command=self.refresh_values).pack(side="left", padx=5)

        # Connection status
        self.connection_label = ttk.Label(left_frame, text="Status: Disconnected", foreground="red")
        self.connection_label.pack(pady=5)
        
        # Lift Visualization
        lift_frame = ttk.LabelFrame(right_frame, text="Lift Visualization", padding="5")
        lift_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.canvas = Canvas(lift_frame, width=250, height=550, bg="lightgray")
        self.canvas.pack(padx=10, pady=10)
        
        # Draw the lift structure
        self.draw_simple_lift(250, 'mid', False)

    def draw_simple_lift(self, lift_y, lift_side, tray_on_lift, tray_y=None):
        # Parameters:
        # lift_y: y-pixel van de lift in de schacht
        # lift_side: 'mid', 'left', 'right'
        # tray_on_lift: True/False
        # tray_y: y-pixel van tray (optioneel, voor animatie)
        self.canvas.delete("all")
        # Layout
        left_x = 20
        mid_x = 120
        right_x = 220
        schacht_top = 30
        schacht_height = 520
        block_h = 20
        # Even locaties links
        even_locs = list(range(-2, 101, 2))
        for i, loc in enumerate(even_locs):
            y = schacht_top + i * block_h
            self.canvas.create_rectangle(left_x, y, left_x+40, y+block_h-2, fill="#e0e0e0", outline="gray")
            self.canvas.create_text(left_x+20, y+block_h/2, text=str(loc), font=("Arial", 8))
        # Oneven locaties rechts
        odd_locs = list(range(-1, 100, 2))
        for i, loc in enumerate(odd_locs):
            y = schacht_top + i * block_h
            self.canvas.create_rectangle(right_x, y, right_x+40, y+block_h-2, fill="#e0e0e0", outline="gray")
            self.canvas.create_text(right_x+20, y+block_h/2, text=str(loc), font=("Arial", 8))
        # Liftschacht
        self.canvas.create_rectangle(mid_x, schacht_top, mid_x+40, schacht_top+len(even_locs)*block_h, outline="blue", width=2)
        # Lift
        lift_x = mid_x if lift_side == 'mid' else (left_x if lift_side == 'left' else right_x)
        self.canvas.create_rectangle(lift_x, lift_y, lift_x+40, lift_y+block_h-2, fill="green", outline="black", width=2)
        # Tray
        if tray_on_lift:
            tray_x = lift_x+5
            tray_y = lift_y+3 if tray_y is None else tray_y
            self.canvas.create_rectangle(tray_x, tray_y, tray_x+30, tray_y+block_h-8, fill="orange", outline="black", width=2)

    async def connect_to_plc(self):
        try:
            # Connect to the PLC simulator on port 4860
            url = "opc.tcp://127.0.0.1:4860"
            logger.info(f"Trying to connect to PLC at {url}")
            self.client = Client(url=url)
            await self.client.connect()
            logger.info(f"Connected to PLC on {url}")
            self.message_queue.put(("connection", "Connected"))
            
            # Get namespace index
            uri = "http://plcsim.example.com"
            self.namespace_index = await self.client.get_namespace_index(uri)
            logger.info(f"Found namespace index: {self.namespace_index}")
            
            # Get PLC node
            plc_node = await self.client.nodes.root.get_child(["0:Objects", f"{self.namespace_index}:PLC"])
            logger.info(f"Found PLC node: {plc_node}")
            
            # Get all variables
            var_names = ["iMainStatus", "xWatchDog", "iStationStatus", "sShortAlarmDescription", 
                       "sAlarmMessage", "sAlarmSolution", "iStatus", "iTaskType", "iOrigination", 
                       "iDestination", "xAcknowledgeMovement", "xTrayInElevator"]
            
            for var_name in var_names:
                try:
                    node = await plc_node.get_child([f"{self.namespace_index}:{var_name}"])
                    self.variables[var_name] = node
                    # Store node ID to variable name mapping
                    node_id_str = str(node.nodeid)
                    self.nodeid_map[node_id_str] = var_name
                    logger.info(f"Found variable: {var_name} with NodeId: {node_id_str}")
                except Exception as var_error:
                    logger.error(f"Error getting variable {var_name}: {var_error}")
            
            # Read initial values
            await self.read_all_values()
            
            # Start subscription
            handler = DataChangeHandler(self.message_queue, self.nodeid_map)
            subscription = await self.client.create_subscription(500, handler)
            
            # Subscribe to each variable individually
            for var_name, var_node in self.variables.items():
                await subscription.subscribe_data_change(var_node)
                logger.info(f"Subscribed to {var_name}")
            
            logger.info("Subscription created and variables monitored")
            
            # Add current position variable for simulation
            # In a real system, this would come from the PLC
            current_position_data = {"variable_name": "iLiftPosition", "value": -2}
            self.message_queue.put(("update", current_position_data))
            
        except Exception as e:
            logger.error(f"Error connecting to PLC: {e}")
            self.message_queue.put(("error", f"Connection error: {e}"))

    async def read_all_values(self):
        """Read all variable values and update UI"""
        for var_name, node in self.variables.items():
            try:
                value = await node.read_value()
                logger.info(f"Read initial value for {var_name}: {value}")
                self.message_queue.put(("update", {"variable_name": var_name, "value": value}))
            except Exception as e:
                logger.error(f"Error reading value for {var_name}: {e}")

    def refresh_values(self):
        """Manually refresh all values from the PLC"""
        asyncio.run_coroutine_threadsafe(self.read_all_values(), self.loop)

    def run_client(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.connect_to_plc())
        self.loop.run_forever()

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
                
            if var_name in self.status_labels:
                self.status_labels[var_name].config(text=str(display_value))
            elif var_name in self.alarm_labels:
                self.alarm_labels[var_name].config(text=str(display_value))
                
            # Update lift state based on status and task type
            if var_name == "iTaskType" and value > 0:
                self.lift_moving = True
                self.draw_simple_lift(250, 'mid', False)
            elif var_name == "iStatus" and value == 3:  # Completed
                self.lift_moving = False
                # Update position based on destination
                try:
                    dest = int(self.destination_var.get())
                    self.current_position = dest
                    self.draw_simple_lift(250, 'mid', False)
                except ValueError:
                    pass
                
        # Handle the node:value format (fallback)
        elif isinstance(data, dict):
            for key, value in data.items():
                # Get variable name from node ID if possible
                node_name = key
                var_name = None
                
                if node_name in self.nodeid_map:
                    var_name = self.nodeid_map[node_name]
                elif isinstance(key, str) and key.startswith("ns="):
                    # Try to get variable name based on nodeId pattern
                    if "ns=2;i=11" in key and isinstance(value, bool):
                        var_name = "xWatchDog"
                    elif "ns=2;i=2" in key:
                        var_name = "iStatus"
                    elif "ns=2;i=3" in key:
                        var_name = "iTaskType"
                    elif "ns=2;i=1" in key:
                        var_name = "iMainStatus"
                    elif "ns=2;i=4" in key:
                        var_name = "iStationStatus"
                
                if var_name:
                    # Apply value translation for specific variables
                    display_value = value
                    if var_name == "iMainStatus" and value in self.system_mode_map:
                        display_value = f"{value} ({self.system_mode_map[value]})"
                    elif var_name == "iTaskType" and value in self.task_type_map:
                        display_value = f"{value} ({self.task_type_map[value]})"
                    elif var_name == "iStatus" and value in self.status_map:
                        display_value = f"{value} ({self.status_map[value]})"
                        
                    if var_name in self.status_labels:
                        self.status_labels[var_name].config(text=str(display_value))
                    elif var_name in self.alarm_labels:
                        self.alarm_labels[var_name].config(text=str(display_value))
                        
                    # Update lift state based on status and task type
                    if var_name == "iTaskType" and value > 0:
                        self.lift_moving = True
                        self.draw_simple_lift(250, 'mid', False)
                    elif var_name == "iStatus" and value == 3:  # Completed
                        self.lift_moving = False
                        # Update position based on destination
                        try:
                            dest = int(self.destination_var.get())
                            self.current_position = dest
                            self.draw_simple_lift(250, 'mid', False)
                        except ValueError:
                            pass

    def start_job(self):
        asyncio.run_coroutine_threadsafe(self._start_job(), self.loop)

    async def _start_job(self):
        try:
            job_type = self.job_type.get()
            origin = int(self.origin_var.get())
            destination = int(self.destination_var.get())
            # Set task type based on selection
            task_type_map = {
                "Full Placement": 1,
                "Move To": 2,
                "Bring Away": 4
            }
            self._pending_job = {
                'job_type': job_type,
                'origin': origin,
                'destination': destination,
                'task_type': task_type_map[job_type]
            }
            # Start de animatie, statusupdate volgt na finish_animation
            self.root.after(0, lambda: self.animate_simple_lift(origin, destination))
        except Exception as e:
            logger.error(f"Failed to start job: {e}")
            messagebox.showerror("Error", f"Failed to start job: {e}")

    def finish_animation(self, destination):
        self.animating = False
        self.current_position = destination
        self.draw_simple_lift(250, 'mid', False)
        # Update PLC-status en variabelen na animatie
        if hasattr(self, '_pending_job'):
            job = self._pending_job
            del self._pending_job
            asyncio.run_coroutine_threadsafe(self._finish_job_plc(job), self.loop)

    async def _finish_job_plc(self, job):
        try:
            await self.variables["iTaskType"].write_value(ua.Variant(0, ua.VariantType.Int32))  # No task
            await self.variables["iStatus"].write_value(ua.Variant(3, ua.VariantType.Int32))  # COMPLETED
            await self.variables["iOrigination"].write_value(ua.Variant(job['origin'], ua.VariantType.Int32))
            await self.variables["iDestination"].write_value(ua.Variant(job['destination'], ua.VariantType.Int32))
            await self.variables["xTrayInElevator"].write_value(ua.Variant(False, ua.VariantType.Boolean))
            logger.info(f"Job finished: {job}")
        except Exception as e:
            logger.error(f"Failed to finish job in PLC: {e}")

    def cancel_job(self):
        asyncio.run_coroutine_threadsafe(self._cancel_job(), self.loop)

    async def _cancel_job(self):
        try:
            await self.variables["iStatus"].write_value(ua.Variant(0, ua.VariantType.Int32))  # IDLE
            await self.variables["iTaskType"].write_value(ua.Variant(0, ua.VariantType.Int32))  # No task
            
            # Update lift state
            self.lift_moving = False
            self.draw_simple_lift(250, 'mid', False)
            
            logger.info("Job cancelled")
        except Exception as e:
            logger.error(f"Failed to cancel job: {e}")
            messagebox.showerror("Error", f"Failed to cancel job: {e}")

    def send_error(self):
        asyncio.run_coroutine_threadsafe(self._send_error(), self.loop)

    async def _send_error(self):
        try:
            await self.variables["iStationStatus"].write_value(ua.Variant(888, ua.VariantType.Int32))  # STATION_ERROR
            await self.variables["sShortAlarmDescription"].write_value("TEST_ERR")
            await self.variables["sAlarmMessage"].write_value("Test Error Message")
            await self.variables["sAlarmSolution"].write_value("Test Error Solution")
            logger.info("Error message sent")
        except Exception as e:
            logger.error(f"Failed to send error: {e}")
            messagebox.showerror("Error", f"Failed to send error: {e}")

    def run(self):
        def check_queue():
            try:
                while True:
                    msg_type, msg = self.message_queue.get_nowait()
                    if msg_type == "error":
                        messagebox.showerror("Error", msg)
                    elif msg_type == "update":
                        self.update_ui(msg)
                    elif msg_type == "connection":
                        self.connection_label.config(text=f"Status: {msg}", foreground="green" if msg == "Connected" else "red")
            except queue.Empty:
                pass
            self.root.after(100, check_queue)

        self.root.after(100, check_queue)
        self.root.mainloop()

    def animate_simple_lift(self, origin, destination):
        # Bepaal y-indexen
        even_locs = list(range(-2, 101, 2))
        odd_locs = list(range(-1, 100, 2))
        block_h = 20
        schacht_top = 30
        # Bepaal of origin/destination even/oneven zijn
        origin_side = 'left' if origin % 2 == 0 else 'right'
        dest_side = 'left' if destination % 2 == 0 else 'right'
        # Vind y-index
        if origin_side == 'left':
            y_idx = even_locs.index(origin)
        else:
            y_idx = odd_locs.index(origin)
        origin_y = schacht_top + y_idx * block_h
        if dest_side == 'left':
            dest_y_idx = even_locs.index(destination)
        else:
            dest_y_idx = odd_locs.index(destination)
        dest_y = schacht_top + dest_y_idx * block_h
        # Startpositie: lift in het midden, op huidige positie
        self.simple_lift_y = origin_y
        self.simple_lift_side = 'mid'
        self.simple_tray_on_lift = False
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        # Stap 1: lift verticaal naar origin_y (als hij daar niet al staat)
        self.root.after(10, lambda: self._simple_lift_move_vert(origin_y, origin_side, dest_y, dest_side))

    def _simple_lift_move_vert(self, target_y, side, dest_y, dest_side):
        step = 4
        if abs(self.simple_lift_y - target_y) < step:
            self.simple_lift_y = target_y
            self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
            # Stap 2: schuif naar zijkant
            self.root.after(200, lambda: self._simple_lift_move_side(side, dest_y, dest_side))
            return
        self.simple_lift_y += step if self.simple_lift_y < target_y else -step
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        self.root.after(10, lambda: self._simple_lift_move_vert(target_y, side, dest_y, dest_side))

    def _simple_lift_move_side(self, side, dest_y, dest_side):
        self.simple_lift_side = side
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        # Tray oppakken
        self.root.after(300, lambda: self._simple_lift_pickup(dest_y, dest_side))

    def _simple_lift_pickup(self, dest_y, dest_side):
        self.simple_tray_on_lift = True
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        # Terug naar midden
        self.root.after(300, lambda: self._simple_lift_return_mid(dest_y, dest_side))

    def _simple_lift_return_mid(self, dest_y, dest_side):
        self.simple_lift_side = 'mid'
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        # Verticaal naar destination
        self.root.after(10, lambda: self._simple_lift_move_vert_dest(dest_y, dest_side))

    def _simple_lift_move_vert_dest(self, target_y, dest_side):
        step = 4
        if abs(self.simple_lift_y - target_y) < step:
            self.simple_lift_y = target_y
            self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
            # Naar zijkant om tray af te zetten
            self.root.after(200, lambda: self._simple_lift_move_side_dest(dest_side))
            return
        self.simple_lift_y += step if self.simple_lift_y < target_y else -step
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        self.root.after(10, lambda: self._simple_lift_move_vert_dest(target_y, dest_side))

    def _simple_lift_move_side_dest(self, dest_side):
        self.simple_lift_side = dest_side
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        # Tray afzetten
        self.root.after(300, self._simple_lift_dropoff)

    def _simple_lift_dropoff(self):
        self.simple_tray_on_lift = False
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        # Terug naar midden en finish
        self.root.after(300, self._simple_lift_finish)

    def _simple_lift_finish(self):
        self.simple_lift_side = 'mid'
        self.draw_simple_lift(self.simple_lift_y, self.simple_lift_side, self.simple_tray_on_lift)
        # Na animatie: update PLC-status
        if hasattr(self, '_pending_job'):
            job = self._pending_job
            del self._pending_job
            asyncio.run_coroutine_threadsafe(self._finish_job_plc(job), self.loop)

class DataChangeHandler:
    def __init__(self, message_queue, nodeid_map):
        self.message_queue = message_queue
        self.nodeid_map = nodeid_map

    async def datachange_notification(self, node, val, data):
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

if __name__ == "__main__":
    simulator = EcoSystemSimulator()
    simulator.run()
