import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import asyncio
import logging
import os
import time
from asyncua import ua
from opcua_client import OPCUAClient
from lift_visualization import LiftVisualizationManager, LIFT1_ID, LIFT2_ID, LIFTS # Import new manager and constants

# Zorg dat de logs map bestaat
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Clear log file at startup
log_filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'ecosystem_gui.log')
# Clear the log file first if it exists
if (os.path.exists(log_filename)):
    try:
        open(log_filename, 'w').close()  # Open en onmiddellijk sluiten om het bestand te wissen
        print(f"Cleared log file: {log_filename}")
    except Exception as e:
        print(f"Warning: Could not clear log file {log_filename}: {e}")

log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler(log_filename, mode='a'), # Log to file (append mode after clearing)
        logging.StreamHandler() # Keep logging to console as well
    ]
)
logger = logging.getLogger("EcoSystemSim_DualLift_ST")

# Set asyncua loggers to a higher level to reduce verbosity
logging.getLogger("asyncua").setLevel(logging.WARNING)
logging.getLogger("asyncua.client.client").setLevel(logging.WARNING)
logging.getLogger("asyncua.client.ua_client").setLevel(logging.WARNING)

PLC_ENDPOINT = "opc.tcp://127.0.0.1:4860/gibas/plc/" # Using port 4860
PLC_NS_URI = "http://gibas.com/plc/"
# LIFT1_ID, LIFT2_ID, LIFTS are now imported from lift_visualization

# Visualisation constants are now in lift_visualization.py
# CANVAS_HEIGHT, CANVAS_WIDTH, etc. are not needed here directly anymore if LiftVisualizationManager handles them internally.

class EcoSystemGUI_DualLift_ST:
    def __init__(self, root):
        self.root = root
        self.root.title("Gibas EcoSystem Simulator (Dual Lift - ST Logic)")
        self.root.geometry("1000x700")

        self.opcua_client = OPCUAClient(PLC_ENDPOINT, PLC_NS_URI)
        self.is_connected = False
        self.monitoring_task = None
        self.last_watchdog_time = {lift_id: 0 for lift_id in LIFTS}
        self.watchdog_timeout = 3.0

        # GUI element storage
        self.lift_frames = {}
        self.status_labels = {}
        self.job_controls = {}
        self.ack_controls = {}
        self.error_controls = {}

        self._setup_gui_layout()
        
        # Initialize LiftVisualizationManager after canvas is created in _setup_gui_layout
        self.lift_vis_manager = LiftVisualizationManager(self.root, self.shared_canvas, LIFTS)

    def _setup_gui_layout(self):
        """Creates the main GUI layout, frames, and widgets."""
        self._create_connection_frame()
        
        main_frame = ttk.Frame(self.root)
        main_frame.pack(expand=True, fill="both", padx=10, pady=5)

        self._create_visualization_frame(main_frame)
        self._create_control_notebook(main_frame)

    def _create_connection_frame(self):
        """Creates the connection management frame."""
        conn_frame = ttk.LabelFrame(self.root, text="Connection", padding=10)
        conn_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(conn_frame, text="PLC Endpoint:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.endpoint_var = tk.StringVar(value=PLC_ENDPOINT)
        ttk.Entry(conn_frame, textvariable=self.endpoint_var, width=40).grid(row=0, column=1, padx=5)
        self.connect_button = ttk.Button(conn_frame, text="Connect", command=self.connect_plc)
        self.connect_button.grid(row=0, column=2, padx=5)
        self.disconnect_button = ttk.Button(conn_frame, text="Disconnect", command=self.disconnect_plc, state=tk.DISABLED)
        self.disconnect_button.grid(row=0, column=3, padx=5)
        self.conn_status_label = ttk.Label(conn_frame, text="Status: Disconnected", foreground="red")
        self.conn_status_label.grid(row=1, column=0, columnspan=4, sticky=tk.W, padx=5, pady=5)

    def _create_visualization_frame(self, parent_frame):
        """Creates the warehouse visualization frame and canvas."""
        vis_frame = ttk.LabelFrame(parent_frame, text="Warehouse Visualization", padding=10)
        vis_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=5)
        from lift_visualization import CANVAS_WIDTH, CANVAS_HEIGHT # Import canvas dimensions
        self.shared_canvas = tk.Canvas(vis_frame, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg='#EAEAEA')
        self.shared_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _create_control_notebook(self, parent_frame):
        """Creates the notebook with tabs for each lift's controls."""
        self.notebook = ttk.Notebook(parent_frame)
        self.notebook.pack(side=tk.RIGHT, expand=True, fill="both", padx=5, pady=5)

        for lift_id in LIFTS:
            lift_tab_frame = ttk.Frame(self.notebook, padding="10")
            self.notebook.add(lift_tab_frame, text=lift_id)
            self.lift_frames[lift_id] = lift_tab_frame
            self._create_lift_controls(lift_tab_frame, lift_id)

    def _create_lift_controls(self, parent_frame, lift_id):
        """Creates the status, job, ack, and error control sections for a single lift."""
        content_frame = ttk.Frame(parent_frame, padding="5") 
        content_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self._create_status_section(content_frame, lift_id)
        self._create_step_info_section(content_frame, lift_id)
        self._create_job_control_section(content_frame, lift_id)
        self._create_ack_section(content_frame, lift_id)
        self._create_error_section(content_frame, lift_id)

    def _create_status_section(self, parent_frame, lift_id):
        """Creates the status display section for a lift."""
        status_vars_to_display = [
            "iCycle", "iStatus", "iElevatorRowLocation", 
            "xTrayInElevator", "iCurrentForkSide",
            "ActiveElevatorAssignment_iTaskType", "ActiveElevatorAssignment_iOrigination",
            "ActiveElevatorAssignment_iDestination"
        ]
        status_frame = ttk.LabelFrame(parent_frame, text=f"{lift_id} Status", padding=10)
        status_frame.pack(fill=tk.X, pady=5)
        self.status_labels[lift_id] = {}
        row_idx, col_idx = 0, 0
        for var_name in status_vars_to_display:
            ttk.Label(status_frame, text=f"{var_name}:").grid(row=row_idx, column=col_idx*2, sticky=tk.W, padx=2, pady=1)
            label = ttk.Label(status_frame, text="N/A", width=25, anchor="w")
            label.grid(row=row_idx, column=col_idx*2+1, sticky=tk.W, padx=2, pady=1)
            self.status_labels[lift_id][var_name] = label
            col_idx += 1
            if col_idx >= 2: 
                col_idx = 0
                row_idx += 1

    def _create_step_info_section(self, parent_frame, lift_id):
        """Creates the step information (comment) section for a lift."""
        step_frame = ttk.LabelFrame(parent_frame, text=f"{lift_id} Stap Informatie", padding=10)
        step_frame.pack(fill=tk.X, pady=5)
        ttk.Label(step_frame, text="sSeq_Step_comment:").pack(side=tk.TOP, anchor=tk.W, padx=2, pady=1)
        seq_step_text = tk.Text(step_frame, height=3, width=90, borderwidth=1, relief="groove")
        seq_step_text.pack(fill=tk.X, padx=2, pady=1)
        seq_step_text.insert("1.0", "N/A")
        seq_step_text.config(state=tk.DISABLED)
        if lift_id not in self.status_labels: self.status_labels[lift_id] = {}
        self.status_labels[lift_id]["sSeq_Step_comment"] = seq_step_text

    def _create_job_control_section(self, parent_frame, lift_id):
        """Creates the job control section for a lift."""
        job_frame = ttk.LabelFrame(parent_frame, text=f"{lift_id} Job Control", padding=10)
        job_frame.pack(fill=tk.X, pady=5)
        controls = {}
        controls['task_type_var'] = tk.IntVar(value=1)
        ttk.Radiobutton(job_frame, text="1: Full Placement", variable=controls['task_type_var'], value=1).grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(job_frame, text="2: Move To", variable=controls['task_type_var'], value=2).grid(row=0, column=2, sticky=tk.W)
        ttk.Radiobutton(job_frame, text="3: Prepare PickUp", variable=controls['task_type_var'], value=3).grid(row=0, column=3, sticky=tk.W)
        ttk.Label(job_frame, text="Task Type:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        controls['origin_var'] = tk.IntVar(value=10 if lift_id == LIFT1_ID else 60)
        ttk.Label(job_frame, text="Origin:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(job_frame, textvariable=controls['origin_var'], width=10).grid(row=1, column=1, sticky=tk.W, padx=5)
        controls['destination_var'] = tk.IntVar(value=50 if lift_id == LIFT1_ID else 20)
        ttk.Label(job_frame, text="Destination:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(job_frame, textvariable=controls['destination_var'], width=10).grid(row=2, column=1, sticky=tk.W, padx=5)
        controls['send_job_button'] = ttk.Button(job_frame, text="Send Job Request", command=lambda l=lift_id: self.send_job(l), state=tk.DISABLED)
        controls['send_job_button'].grid(row=3, column=0, columnspan=2, pady=10)
        controls['clear_task_button'] = ttk.Button(job_frame, text="Clear Task (Reset PLC)", command=lambda l=lift_id: self.clear_task(l), state=tk.DISABLED)
        controls['clear_task_button'].grid(row=3, column=2, columnspan=2, pady=10)
        self.job_controls[lift_id] = controls

    def _create_ack_section(self, parent_frame, lift_id):
        """Creates the handshake/acknowledge section for a lift."""
        ack_frame = ttk.LabelFrame(parent_frame, text=f"{lift_id} Handshake / Acknowledge", padding=10)
        ack_frame.pack(fill=tk.X, pady=5)
        ack_ctrls = {}
        ack_ctrls['ack_movement_button'] = ttk.Button(ack_frame, text="Acknowledge Job Step", command=lambda l=lift_id: self.acknowledge_job_step(l), state=tk.DISABLED)
        ack_ctrls['ack_movement_button'].pack(side=tk.LEFT, padx=10)
        ack_ctrls['ack_info_label'] = ttk.Label(ack_frame, text="PLC Awaiting Ack: No", foreground="grey")
        ack_ctrls['ack_info_label'].pack(side=tk.LEFT, padx=10)
        self.ack_controls[lift_id] = ack_ctrls

    def _create_error_section(self, parent_frame, lift_id):
        """Creates the error control section for a lift."""
        error_frame = ttk.LabelFrame(parent_frame, text=f"{lift_id} Error Control", padding=10)
        error_frame.pack(fill=tk.X, pady=5)
        err_ctrls = {}
        err_ctrls['clear_error_button'] = ttk.Button(error_frame, text="Clear Error", command=lambda l=lift_id: self.clear_error(l), state=tk.DISABLED)
        err_ctrls['clear_error_button'].pack(side=tk.LEFT, padx=10)
        err_ctrls['error_status_label'] = ttk.Label(error_frame, text="PLC Error State: No", foreground="green")
        err_ctrls['error_status_label'].pack(side=tk.LEFT, padx=10)
        self.error_controls[lift_id] = err_ctrls

    # --- GUI Layout and Element Creation End, Business Logic Methods Below ---

    def _safe_get_int_from_data(self, plc_data, key, default=0):
        """Safely get an integer from PLC data dictionary."""
        val = plc_data.get(key, default)
        if val is None or isinstance(val, str) or not isinstance(val, (int, float)):
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _update_lift_text_labels(self, lift_id, plc_data):
        """Updates the text-based status labels for a given lift."""
        for name, label_widget in self.status_labels[lift_id].items():
            value = plc_data.get(name, "N/A")
            if name == "sSeq_Step_comment" and value == "": value = "(No comment)"
            elif name == "sSeq_Step_comment": value = str(value).strip()
            elif name.startswith("s") and isinstance(value, bytes): 
                value = value.decode('utf-8', 'ignore').strip()
            if isinstance(value, float): value = f"{value:.2f}"
            
            if isinstance(label_widget, tk.Text):
                label_widget.config(state=tk.NORMAL)
                label_widget.delete("1.0", tk.END)
                label_widget.insert("1.0", str(value))
                label_widget.config(state=tk.DISABLED)
            else:
                label_widget.config(text=str(value))

    def _update_lift_button_states(self, lift_id, plc_data):
        """Updates the state of buttons (ack, error, job) for a given lift."""
        ack_type = self._safe_get_int_from_data(plc_data, "EcoAck_iAssingmentType")
        ack_row = self._safe_get_int_from_data(plc_data, "EcoAck_iRowNr")
        ack_needed = ack_type > 0
        error_code = self._safe_get_int_from_data(plc_data, "iErrorCode")
        is_error = error_code != 0
        plc_cycle = self._safe_get_int_from_data(plc_data, "iCycle", -1)

        # Ack controls
        ack_info_text = "PLC Awaiting Ack: No"
        ack_button_state = tk.DISABLED
        ack_label_color = "grey"
        if ack_needed and self.is_connected:
            ack_type_str = "GetTray" if ack_type == 1 else "SetTray" if ack_type == 2 else f"Type {ack_type}"
            ack_info_text = f"PLC Awaiting Ack: {ack_type_str} @ Row {ack_row}"
            ack_button_state = tk.NORMAL
            ack_label_color = "orange"
        if lift_id in self.ack_controls:
             self.ack_controls[lift_id]['ack_info_label'].config(text=ack_info_text, foreground=ack_label_color)
             self.ack_controls[lift_id]['ack_movement_button'].config(state=ack_button_state)

        # Error controls
        error_info_text = f"PLC Error State: No (Code: {error_code})"
        error_button_state = tk.DISABLED
        error_label_color = "green"
        if is_error and self.is_connected:
             error_info_text = f"PLC Error State: YES (Code: {error_code})"
             error_button_state = tk.NORMAL
             error_label_color = "red"
        if lift_id in self.error_controls:
             self.error_controls[lift_id]['error_status_label'].config(text=error_info_text, foreground=error_label_color)
             self.error_controls[lift_id]['clear_error_button'].config(state=error_button_state)

        # Job controls
        can_send_job = self.is_connected and not is_error and plc_cycle == 10 
        job_button_state = tk.NORMAL if can_send_job else tk.DISABLED
        if lift_id in self.job_controls:
             self.job_controls[lift_id]['send_job_button'].config(state=job_button_state)

        waiting_clear_comment = str(plc_data.get("sSeq_Step_comment", ""))
        waiting_clear = ("Done - Waiting" in waiting_clear_comment or "Rejected" in waiting_clear_comment)
        needs_clear = self.is_connected and ((plc_cycle not in [0, 10] and plc_cycle > 0) or waiting_clear)
        clear_button_state = tk.NORMAL if needs_clear else tk.DISABLED
        if lift_id in self.job_controls:
            self.job_controls[lift_id]['clear_task_button'].config(state=clear_button_state)

    def _update_gui_status(self, lift_id, plc_data):
        if lift_id not in self.status_labels: return

        self._update_lift_text_labels(lift_id, plc_data)
        self._update_lift_button_states(lift_id, plc_data)

        # Delegate Visualization Update to LiftVisualizationManager
        current_row = self._safe_get_int_from_data(plc_data, "iElevatorRowLocation")
        has_tray = plc_data.get("xTrayInElevator", False)
        fork_side = self._safe_get_int_from_data(plc_data, "iCurrentForkSide", 0)
        is_error = self._safe_get_int_from_data(plc_data, "iErrorCode") != 0
        
        self.lift_vis_manager.update_lift_visual_state(lift_id, current_row, has_tray, fork_side, is_error)

    async def _monitor_plc(self):
        logger.info("Starting Dual Lift PLC monitoring task (ST Logic).")
        variables_to_read_per_lift = list(self.status_labels[LIFT1_ID].keys())
        handshake_vars = ["EcoAck_iAssingmentType", "EcoAck_iRowNr", "EcoAck_xAcknowldeFromEco", 
                         "iErrorCode", "xWatchDog"]
        for var in handshake_vars:
            if var not in variables_to_read_per_lift:
                variables_to_read_per_lift.append(var)
        logger.info(f"Monitoring variables: {variables_to_read_per_lift}")

        test_id = f"{LIFT1_ID}/iCycle"
        test_val = await self.opcua_client.read_value(test_id)
        if test_val is None:
             logger.error(f"INITIAL READ FAILED for {test_id}. Aborting monitor.")
             self._handle_connection_error()
             return
        else:
             logger.info(f"Initial read successful: {test_id} = {test_val}")

        while self.opcua_client.is_connected: 
            try:
                current_time = time.time()
                all_lift_data = {}
                any_critical_read_failed = False

                for lift_id_loop in LIFTS:
                    lift_data = {}
                    watchdog_ok_for_lift = False
                    for var_name in variables_to_read_per_lift:
                        node_identifier = f"{lift_id_loop}/{var_name}"
                        value = await self.opcua_client.read_value(node_identifier)
                        if value is not None:
                            lift_data[var_name] = value
                            if var_name == "xWatchDog" and value: 
                                self.last_watchdog_time[lift_id_loop] = current_time
                                watchdog_ok_for_lift = True
                        else:
                            logger.warning(f"Failed to read {node_identifier} during monitoring.")
                            if var_name in ["iCycle", "iStatus", "iElevatorRowLocation"]:
                                logger.error(f"Critical variable {node_identifier} read failed.")
                                any_critical_read_failed = True
                                break 
                    
                    if any_critical_read_failed: break 

                    if not watchdog_ok_for_lift and (current_time - self.last_watchdog_time.get(lift_id_loop, 0)) > self.watchdog_timeout:
                        logger.error(f"Watchdog timeout for {lift_id_loop}! Last seen: {self.last_watchdog_time.get(lift_id_loop, 'never')}")
                        any_critical_read_failed = True 
                        break
                    
                    all_lift_data[lift_id_loop] = lift_data
                
                if any_critical_read_failed:
                    self._handle_connection_error()
                    break

                for lift_id_gui, data_gui in all_lift_data.items():
                    if data_gui: 
                        self.root.after(0, self._update_gui_status, lift_id_gui, data_gui)
                
                await asyncio.sleep(0.2)

            except asyncio.CancelledError:
                logger.info("PLC monitoring task cancelled.")
                break
            except Exception as e:
                logger.exception(f"Error in PLC monitoring loop: {e}")
                self._handle_connection_error()
                break

        logger.info("PLC monitoring task stopped.")
        if self.opcua_client.is_connected:
             self._update_connection_status(False)

    async def _async_connect(self):
        try:
            self.opcua_client.endpoint_url = self.endpoint_var.get()
            connected = await self.opcua_client.connect()
            if connected:
                logger.info("Waiting 1 second before starting monitor...")
                await asyncio.sleep(1.0)
                self._update_connection_status(True)
                now = time.time()
                for lift_id in LIFTS: self.last_watchdog_time[lift_id] = now
                if self.monitoring_task: self.monitoring_task.cancel()
                self.monitoring_task = asyncio.create_task(self._monitor_plc())
            else:
                messagebox.showerror("Connection Failed", f"Could not connect to {self.endpoint_var.get()}\nError: OPCUAClient connection failed.")
                self._update_connection_status(False)
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            messagebox.showerror("Connection Failed", f"Could not connect to {self.endpoint_var.get()}\nError: {e}")
            await self.opcua_client.disconnect()
            self._update_connection_status(False)

    def _handle_connection_error(self):
         if self.opcua_client.is_connected:
             logger.error("Connection error detected.")
             if self.root.winfo_exists() and not getattr(self, '_disconnecting_flag', False):
                 messagebox.showerror("Connection Error", "Lost connection to PLC or failed to communicate.")
             self.disconnect_plc()

    def _reset_lift_gui_elements(self, lift_id):
        """Resets all GUI elements for a specific lift to their default/disconnected state."""
        # Reset status labels to N/A
        empty_data = {name: "N/A" for name in self.status_labels[lift_id].keys()}
        self._update_lift_text_labels(lift_id, empty_data)

        # Reset visualization to default (row 1, no tray, no error)
        self.lift_vis_manager.update_lift_visual_state(lift_id, 1, False, 0, False)
        self.lift_vis_manager.last_position[lift_id] = 1 # Also reset logical position in manager

        # Disable buttons and reset associated labels
        if lift_id in self.job_controls:
            self.job_controls[lift_id]['send_job_button'].config(state=tk.DISABLED)
            self.job_controls[lift_id]['clear_task_button'].config(state=tk.DISABLED)
        if lift_id in self.ack_controls:
            self.ack_controls[lift_id]['ack_movement_button'].config(state=tk.DISABLED)
            self.ack_controls[lift_id]['ack_info_label'].config(text="PLC Awaiting Ack: No", foreground="grey")
        if lift_id in self.error_controls:
            self.error_controls[lift_id]['clear_error_button'].config(state=tk.DISABLED)
            self.error_controls[lift_id]['error_status_label'].config(text="PLC Error State: No", foreground="green")

    def _update_connection_status(self, connected):
        self.is_connected = connected
        status_text = "Status: Connected" if connected else "Status: Disconnected"
        status_color = "green" if connected else "red"
        btn_conn_state = tk.DISABLED if connected else tk.NORMAL
        btn_disconn_state = tk.NORMAL if connected else tk.DISABLED

        if self.root.winfo_exists():
            self.conn_status_label.config(text=status_text, foreground=status_color)
            self.connect_button.config(state=btn_conn_state)
            self.disconnect_button.config(state=btn_disconn_state)

            if not connected:
                for lift_id in LIFTS:
                    self._reset_lift_gui_elements(lift_id)
            # If connected, PLC monitoring will update individual button states via _update_gui_status
        else:
            logger.warning("_update_connection_status called but root window does not exist.")

    def connect_plc(self):
        endpoint = self.endpoint_var.get()
        logger.info(f"Attempting to connect to {endpoint}")
        self.opcua_client.endpoint_url = endpoint
        asyncio.create_task(self._async_connect())

    async def _async_disconnect(self):
         logger.info("Async disconnect initiated.")
         self._disconnecting_flag = True
         await self.opcua_client.disconnect()
         self._update_connection_status(False) # This will call _reset_lift_gui_elements
         logger.info("Async disconnect completed.")
         if hasattr(self, '_disconnecting_flag'): # Clean up flag
            delattr(self, '_disconnecting_flag')

    def disconnect_plc(self):
        logger.info("Disconnecting from PLC...")
        if self.monitoring_task:
            self.monitoring_task.cancel()
            self.monitoring_task = None
        if self.opcua_client.is_connected:
            asyncio.create_task(self._async_disconnect())
        else:
            # Ensure GUI is reset even if opcua_client thought it was already disconnected
            self._update_connection_status(False)

    def send_job(self, lift_id):
        if not self.opcua_client.is_connected:
             messagebox.showwarning("OPC UA", "Not connected to PLC.")
             return
        controls = self.job_controls[lift_id]
        task_type = controls['task_type_var'].get()
        origin = controls['origin_var'].get()
        destination = controls['destination_var'].get()
        logger.info(f"Sending Job to {lift_id}: Type={task_type}, Origin={origin}, Dest={destination}")
        async def job_write_sequence():
            await self.opcua_client.write_value(f"{lift_id}/Eco_iTaskType", task_type, ua.VariantType.Int16)
            await self.opcua_client.write_value(f"{lift_id}/Eco_iOrigination", origin, ua.VariantType.Int16)
            await self.opcua_client.write_value(f"{lift_id}/Eco_iDestination", destination, ua.VariantType.Int16)
            # Verwijderd: Eco_xJobRequest bestaat niet in de PLC en is niet nodig
            # await self.opcua_client.write_value(f"{lift_id}/Eco_xJobRequest", True, ua.VariantType.Boolean)
        asyncio.create_task(job_write_sequence())

    def acknowledge_job_step(self, lift_id):
        if not self.opcua_client.is_connected:
             messagebox.showwarning("OPC UA", "Not connected to PLC.")
             return
        logger.info(f"Sending Job Step Acknowledge for {lift_id} (EcoAck_xAcknowldeFromEco = True)")
        async def send_acknowledge():
            success = await self.opcua_client.write_value(f"{lift_id}/EcoAck_xAcknowldeFromEco", True, ua.VariantType.Boolean)
            if not success:
                messagebox.showerror("OPC UA Error", f"Failed to send Acknowledge for {lift_id}.")
            else:
                logger.info(f"Acknowledge sent for {lift_id}.")
        asyncio.create_task(send_acknowledge())

    def clear_error(self, lift_id):
        if not self.opcua_client.is_connected:
            messagebox.showwarning("OPC UA", "Not connected to PLC.")
            return
        logger.info(f"Sending Clear Error Request for {lift_id} (xClearError = True)")
        asyncio.create_task(self.opcua_client.write_value(f"{lift_id}/xClearError", True, ua.VariantType.Boolean))

    def clear_task(self, lift_id):
        if not self.opcua_client.is_connected:
            messagebox.showwarning("OPC UA", "Not connected to PLC.")
            return
        logger.info(f"Stuur Clear Task (TaskType=0) naar {lift_id}")
        async def clear_task_sequence():
            success_type = await self.opcua_client.write_value(f"{lift_id}/Eco_iTaskType", 0, ua.VariantType.Int16)
            if not success_type:
                messagebox.showerror("OPC UA Error", f"Failed to set TaskType to 0 for {lift_id}.")
                return
            logger.info(f"Clear Task sequence sent to {lift_id}.")
        asyncio.create_task(clear_task_sequence())

# create_rack_visualization is removed as its logic is in LiftVisualizationManager._setup_warehouse_visualization

async def run_gui(root):
    while True:
        try:
            if not root.winfo_exists(): break 
            root.update() 
            await asyncio.sleep(0.01)
        except tk.TclError as e:
             if "application has been destroyed" in str(e).lower() or "invalid command name" in str(e).lower():
                 logger.info("GUI main loop: Root window destroyed or invalid command, exiting loop.")
                 break
             else:
                 logger.exception("GUI main loop: TclError occurred.")
                 raise
        except Exception as e:
            logger.exception("GUI main loop: Unexpected error.")
            break
    logger.info("Exited run_gui loop.")

async def main():
    root = tk.Tk()
    gui = EcoSystemGUI_DualLift_ST(root)
    
    async def on_closing_async(): 
        logger.info("Close button clicked (async handler).")
        if gui.opcua_client and gui.opcua_client.is_connected:
            logger.info("OPC UA client is connected, attempting async disconnect.")
            await gui.opcua_client.disconnect() 
        if root.winfo_exists():
            root.destroy()
        logger.info("Window destroyed after potential disconnect.")

    def on_closing_sync_wrapper(): 
        logger.info("WM_DELETE_WINDOW triggered.")
        if asyncio.get_event_loop().is_running():
            asyncio.create_task(on_closing_async())
        else:
            logger.warning("Event loop not running during on_closing. Destroying root directly.")
            if root.winfo_exists(): root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing_sync_wrapper)
    
    gui_task = asyncio.create_task(run_gui(root))
    
    try: 
        await gui_task
    except asyncio.CancelledError: 
        logger.info("GUI task cancelled.")
    except Exception as e:
        logger.error(f"Error in GUI task: {e}", exc_info=True)
    finally:
        logger.info("Main finally block reached.")
        if gui.opcua_client and gui.opcua_client.is_connected:
             logger.info("Main finally: OPC UA client connected, ensuring disconnect.")
             try:
                loop = asyncio.get_event_loop()
                if loop.is_running() and not loop.is_closed():
                    disconnect_task = asyncio.create_task(gui.opcua_client.disconnect())
                    await asyncio.wait_for(disconnect_task, timeout=2.0) 
                elif not gui.opcua_client.is_connected: 
                    pass
                else:
                    logger.info("Main finally: Event loop not running, attempting blocking disconnect.")
                    asyncio.run(gui.opcua_client.disconnect()) 
             except asyncio.TimeoutError:
                logger.error("Main finally: Timeout during final disconnect attempt.")
             except RuntimeError as e_rt:
                logger.error(f"Main finally: Runtime error during disconnect: {e_rt}. Might be due to event loop state.")
             except Exception as e_final_disconnect:
                 logger.error(f"Main finally: Error during final disconnect attempt: {e_final_disconnect}", exc_info=True)

        if root.winfo_exists(): 
            logger.info("Main finally: Destroying root window if it still exists.")
            root.destroy()
        logger.info("Application shutdown sequence complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application terminated by KeyboardInterrupt.")
    except tk.TclError as e:
        if "application has been destroyed" not in str(e).lower() and "invalid command name" not in str(e).lower():
            logger.exception("Unhandled TclError in __main__:")
    except Exception as e:
        logger.exception("Unhandled exception in __main__:")
    finally:
        logger.info("Application exiting __main__.")
