import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import asyncio
import logging
import os
import time
from asyncua import ua
from opcua_client import OPCUAClient
from lift_visualization import LiftVisualizationManager, LIFT1_ID, LIFT2_ID, LIFTS # Import new manager and constants
from auto_mode import add_auto_mode_to_gui  # Import de auto mode functie

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

        self.lift_frames = {}
        self.status_labels = {}
        self.job_controls = {}
        self.ack_controls = {}
        self.error_controls = {}
        self._setup_gui_layout()
        
        # Initialize LiftVisualizationManager after canvas is created in _setup_gui_layout
        self.lift_vis_manager = LiftVisualizationManager(self.root, self.shared_canvas, LIFTS)

        # Add auto mode functionality to the GUI
        self.auto_mode_manager = add_auto_mode_to_gui(self)

    def _setup_gui_layout(self):
        """Creates the main GUI layout, frames, and widgets."""
        self._create_connection_frame()
        main_frame = ttk.Frame(self.root)
        main_frame.pack(expand=True, fill="both", padx=10, pady=5)
        self._create_visualization_frame(main_frame)
        self._create_control_notebook(main_frame)

    def _create_connection_frame(self):
        """Creates the connection management frame.""" # Corrected docstring quote
        conn_frame = ttk.LabelFrame(self.root, text="Connection", padding=10)
        conn_frame.pack(fill=tk.X, padx=10, pady=5)
        # Row 0 for endpoint
        ttk.Label(conn_frame, text="PLC Endpoint:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.endpoint_var = tk.StringVar(value=PLC_ENDPOINT)
        ttk.Entry(conn_frame, textvariable=self.endpoint_var, width=40).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        # Row 1 for buttons
        self.connect_button = ttk.Button(conn_frame, text="Connect", command=self.connect_plc)
        self.connect_button.grid(row=0, column=2, padx=5, pady=2)
        self.disconnect_button = ttk.Button(conn_frame, text="Disconnect", command=self.disconnect_plc, state=tk.DISABLED)
        self.disconnect_button.grid(row=0, column=3, padx=5, pady=2)
        # Row 2 for status labels
        self.connection_status_label = ttk.Label(conn_frame, text="Status: Disconnected", foreground="red")
        self.connection_status_label.grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=5, pady=2)

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
        self._create_error_display_section(content_frame, lift_id) # Renamed from _create_error_section

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
        
        # Standaardwaarden voor origin en destination - gebruik geldige standaardwaarden
        controls['origin_var'] = tk.IntVar(value=5)
        ttk.Label(job_frame, text="Origin:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(job_frame, textvariable=controls['origin_var'], width=10).grid(row=1, column=1, sticky=tk.W, padx=5)
        
        controls['destination_var'] = tk.IntVar(value=90)
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

    def _create_error_display_section(self, parent_frame, lift_id): # Renamed from _create_error_section
        """Creates the error display section for a lift.""" 
        error_frame = ttk.LabelFrame(parent_frame, text=f"{lift_id} Error Display", padding=10) 
        error_frame.pack(fill=tk.X, pady=5)
        err_ctrls = {}
        
        # Status display
        top_frame = ttk.Frame(error_frame)
        top_frame.pack(fill=tk.X, pady=5)
        
        err_ctrls['error_status_label'] = ttk.Label(top_frame, text="PLC Error State: No", foreground="green")
        err_ctrls['error_status_label'].pack(side=tk.LEFT, padx=10)
        
        # Error details sectie toevoegen
        details_frame = ttk.Frame(error_frame)
        details_frame.pack(fill=tk.X, pady=5)
        
        # Korte beschrijving
        ttk.Label(details_frame, text="Korte beschrijving:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        err_ctrls['short_description'] = ttk.Label(details_frame, text="None", foreground="gray")
        err_ctrls['short_description'].grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        
        # Uitgebreide foutmelding
        ttk.Label(details_frame, text="Foutmelding:").grid(row=1, column=0, sticky=tk.NW, padx=5, pady=2)
        err_ctrls['message'] = tk.Text(details_frame, height=2, width=50, borderwidth=1, relief="groove")
        err_ctrls['message'].grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        err_ctrls['message'].config(state=tk.DISABLED)
        
        # Oplossing
        ttk.Label(details_frame, text="Oplossing:").grid(row=2, column=0, sticky=tk.NW, padx=5, pady=2)
        err_ctrls['solution'] = tk.Text(details_frame, height=2, width=50, borderwidth=1, relief="groove")
        err_ctrls['solution'].grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)
        err_ctrls['solution'].config(state=tk.DISABLED)
        
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
        """Updates the state of buttons (ack, job) and error display for a given lift."""
        # Gebruik de correcte interface-variabelen volgens interface.txt
        ack_type = self._safe_get_int_from_data(plc_data, "iJobType")  # Deel van Handshake struct
        ack_row = self._safe_get_int_from_data(plc_data, "iRowNr")     # Deel van Handshake struct
        ack_needed = ack_type > 0
        error_code = self._safe_get_int_from_data(plc_data, "iErrorCode")
        is_error = error_code != 0
        plc_cycle = self._safe_get_int_from_data(plc_data, "iCycle", -1)
        iStationStatus = self._safe_get_int_from_data(plc_data, "iStationStatus") # STATUS_OK=1, STATUS_WARNING=2, STATUS_ERROR=3

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

        # Error and Status display logic
        current_status_text = "PLC Status: Unknown"
        current_status_color = "gray"
        populate_details = False

        station_state_desc_raw = plc_data.get("sStationStateDescription", "")
        if isinstance(station_state_desc_raw, bytes):
            station_state_desc = station_state_desc_raw.decode('utf-8', 'ignore').strip()
        else:
            station_state_desc = str(station_state_desc_raw).strip()

        if not self.is_connected:
            current_status_text = "PLC Status: Disconnected"
            current_status_color = "gray"
            populate_details = False
        elif is_error: # PLC reported a hard error
            current_status_text = f"PLC Error State: YES (Code: {error_code})"
            if station_state_desc:
                 current_status_text = f"PLC Status: {station_state_desc} (Code: {error_code})"
            current_status_color = "red"
            populate_details = True
        elif iStationStatus == 2:  # STATUS_WARNING (e.g., Job Rejected)
            current_status_text = f"PLC Status: {station_state_desc or 'Warning'}"
            current_status_color = "orange"
            populate_details = True
        elif iStationStatus == 1: # STATUS_OK
            current_status_text = f"PLC Status: {station_state_desc or 'OK'} (Cycle: {plc_cycle})"
            current_status_color = "green"
            populate_details = False
        else: # Other states or default
            current_status_text = f"PLC Status: {station_state_desc or 'OK'} (Cycle: {plc_cycle}, Status: {iStationStatus})"
            current_status_color = "blue" # Or some other default color
            populate_details = False


        if lift_id in self.error_controls:
            self.error_controls[lift_id]['error_status_label'].config(text=current_status_text, foreground=current_status_color)

            if populate_details:
                short_desc_raw = plc_data.get("sShortAlarmDescription", "")
                if isinstance(short_desc_raw, bytes):
                    short_desc = short_desc_raw.decode('utf-8', 'ignore').strip()
                else:
                    short_desc = str(short_desc_raw).strip()
                
                error_msg_raw = plc_data.get("sAlarmMessage", "")
                if isinstance(error_msg_raw, bytes):
                    error_msg = error_msg_raw.decode('utf-8', 'ignore').strip()
                else:
                    error_msg = str(error_msg_raw).strip()
                    
                solution_raw = plc_data.get("sAlarmSolution", "")
                if isinstance(solution_raw, bytes):
                    solution = solution_raw.decode('utf-8', 'ignore').strip()
                else:
                    solution = str(solution_raw).strip()
                
                detail_text_color = current_status_color # Match status color, or choose e.g. "black"
                self.error_controls[lift_id]['short_description'].config(text=short_desc or "N/A", foreground=detail_text_color)
                
                msg_widget = self.error_controls[lift_id]['message']
                msg_widget.config(state=tk.NORMAL)
                msg_widget.delete("1.0", tk.END)
                msg_widget.insert("1.0", error_msg or "Geen details beschikbaar")
                msg_widget.config(state=tk.DISABLED)
                
                sol_widget = self.error_controls[lift_id]['solution']
                sol_widget.config(state=tk.NORMAL)
                sol_widget.delete("1.0", tk.END)
                sol_widget.insert("1.0", solution or "Geen oplossing beschikbaar")
                sol_widget.config(state=tk.DISABLED)
            else:
                # Reset error details if not populating
                self.error_controls[lift_id]['short_description'].config(text="None", foreground="gray")
                
                msg_widget = self.error_controls[lift_id]['message']
                msg_widget.config(state=tk.NORMAL)
                msg_widget.delete("1.0", tk.END)
                msg_widget.insert("1.0", "")
                msg_widget.config(state=tk.DISABLED)
                
                sol_widget = self.error_controls[lift_id]['solution']
                sol_widget.config(state=tk.NORMAL)
                sol_widget.delete("1.0", tk.END)
                sol_widget.insert("1.0", "")
                sol_widget.config(state=tk.DISABLED)
                
        # Job controls - Wijziging: Niet automatisch een nieuwe job sturen na voltooiing
        # Wanneer de PLC in de gereedstatus is (iCycle = 10), sturen we ALLEEN een nieuwe job
        # als de gebruiker expliciet op de knop drukt
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
        
        # Watchdog status is now handled by overall connection status.
        # If OPC UA reads/writes fail, the connection status will reflect that.

        # Delegate Visualization Update to LiftVisualizationManager
        current_row = self._safe_get_int_from_data(plc_data, "iElevatorRowLocation")
        has_tray = plc_data.get("xTrayInElevator", False)
        fork_side = self._safe_get_int_from_data(plc_data, "iCurrentForkSide", 0)
        is_error = self._safe_get_int_from_data(plc_data, "iErrorCode") != 0
        
        self.lift_vis_manager.update_lift_visual_state(lift_id, current_row, has_tray, fork_side, is_error)

    async def _monitor_plc(self):
        logger.info("Starting Dual Lift PLC monitoring task (ST Logic).")
        
        # Define system interface variables according to interface.txt
        # xWatchDog is written by EcoSystem, so not read here.
        interface_vars = [
            "iAmountOfSations",
            "iMainStatus",
            "iCancelAssignment"
            # "xWatchDog"  # Removed: EcoSystem WRITES this, PLC READS it.
        ]
        
        # Define station data variables according to interface.txt structure
        station_vars = [
            "StationData.iCycle",
            "StationData.iStationStatus", 
            "StationData.sStationStateDescription",
            "StationData.sShortAlarmDescription", 
            "StationData.sAlarmSolution",
            "StationData.Handshake.iRowNr",
            "StationData.Handshake.iJobType"
        ]
        
        # Define elevator variables that aren't in interface.txt but needed for visualization
        # These won't be monitored directly as they're not part of the interface
        internal_vars = [
            "iElevatorRowLocation",
            "xTrayInElevator",
            "iCurrentForkSide",
            "iErrorCode",
            "sSeq_Step_comment"
        ]
        
        logger.info(f"Monitoring system interface variables: {interface_vars}")
        logger.info(f"Monitoring station data variables: {station_vars}")
        logger.info(f"Internal visualization variables (not monitored directly): {internal_vars}")

        # Test the connection with a system variable
        test_id = "iMainStatus"
        logger.info(f"Testing initial connection with {test_id}")
        test_val = await self.opcua_client.read_value(test_id)
        if test_val is None:
            logger.error(f"INITIAL READ FAILED for {test_id}. Aborting monitor.")
            self._handle_connection_error()
            return
        else:
            logger.info(f"Initial read successful: {test_id} = {test_val}")
        
        # Main monitoring loop
        while self.opcua_client.is_connected: 
            try:
                # Send EcoSystem watchdog signal to PLC
                ecosystem_watchdog_sent_ok = await self.opcua_client.write_value("xWatchDog", True, ua.VariantType.Boolean)
                if not ecosystem_watchdog_sent_ok:
                    logger.warning("Failed to send EcoSystem watchdog signal (xWatchDog) to PLC. Assuming connection issue.")
                    any_critical_read_failed = True # This will trigger _handle_connection_error in the loop
                
                current_time = time.time()
                all_lift_data = {}
                if 'any_critical_read_failed' not in locals(): # ensure it's defined if the watchdog write was the first operation
                    any_critical_read_failed = False
                # system_watchdog_ok = False # Removed: Old logic

                # Read system variables
                sys_data = {}
                for var_name in interface_vars:
                    value = await self.opcua_client.read_value(var_name)
                    if value is not None:
                        sys_data[var_name] = value
                        # if var_name == "xWatchDog": # Removed block
                            # if value: 
                            #     self.last_watchdog_time["System"] = current_time
                            #     system_watchdog_ok = True
                            # If value is False, system_watchdog_ok remains False unless updated by timeout check
                    # elif var_name == "xWatchDog": # Removed block
                        # logger.warning("Failed to read system xWatchDog variable.")
                        # system_watchdog_ok = False
                    elif value is None and var_name in ["iMainStatus"]: # Example of a critical variable
                        logger.error(f"CRITICAL READ FAILED for {var_name}. Aborting monitor for this cycle.")
                        any_critical_read_failed = True
                        break # Break from reading system vars, will then break main loop via any_critical_read_failed


                if any_critical_read_failed: # Check if critical read failed or watchdog send failed
                    self._handle_connection_error()
                    break

                # Check system watchdog timeout - REMOVED entire block
                # if not system_watchdog_ok and (current_time - self.last_watchdog_time.get("System", 0)) > self.watchdog_timeout:
                #    logger.error(f"System Watchdog timeout! Last seen: {self.last_watchdog_time.get('System', 'never')}")
                #    system_watchdog_ok = False 
                #    any_critical_read_failed = True 

                # Update central watchdog display - REMOVED
                # self.root.after(0, self._update_watchdog_display, system_watchdog_ok)

                # Read station data for each lift
                for lift_id_loop in LIFTS:
                    lift_data = {}
                    # watchdog_ok_for_lift = False # Removed

                    # Include system variables in lift data
                    for key, value in sys_data.items():
                        lift_data[key] = value
                      
                    # Try different paths for finding StationData variables
                    for var_name in station_vars:
                        # Extract the base name for storing in our dictionary
                        base_name = var_name.split('.')[-1]
                        
                        # Try these access paths in order until one works
                        paths_to_try = [
                            f"{lift_id_loop}/StationData/{base_name}",  # Lift1/StationData/iCycle - This one works!
                            f"{lift_id_loop}/{var_name}",               # Lift1/StationData.iCycle
                            f"{lift_id_loop}.{var_name}",               # Lift1.StationData.iCycle
                            f"{var_name}",                              # StationData.iCycle
                        ]
                        
                        # For Handshake variables, add more specific paths
                        if "Handshake" in var_name:
                            handshake_base_name = var_name.split('.')[-1]
                            handshake_paths = [
                                f"{lift_id_loop}/StationData/Handshake/{handshake_base_name}",  # This path works!
                                f"{lift_id_loop}.StationData.Handshake.{handshake_base_name}"   
                            ]
                            paths_to_try = handshake_paths + paths_to_try  # Try Handshake specific paths first
                            

                        # Try each path until we find a value
                        value = None # Reset before trying paths for this var_name
                        successful_path = None
                        for path in paths_to_try:
                            value = await self.opcua_client.read_value(path)
                            if value is not None:
                                successful_path = path # Store the path that worked
                                break # Exit loop on first success
                        
                        # Store the value if found with any path
                        if value is not None:
                            lift_data[base_name] = value
                        else:
                            logger.warning(f"Failed to read {var_name} for {lift_id_loop} after trying paths: {paths_to_try}")
                    
                    # Also try to read the internal variables needed for visualization
                    for var_name in internal_vars:
                        # Try multiple path formats
                        paths_to_try = [
                            f"{lift_id_loop}/{var_name}",  # Lift1/iElevatorRowLocation
                            f"{lift_id_loop}.{var_name}"   # Lift1.iElevatorRowLocation
                        ]
                        value = None
                        for path in paths_to_try:
                            value = await self.opcua_client.read_value(path)
                            if value is not None:
                                break
                        
                        if value is not None:
                            lift_data[var_name] = value
                  
                    # Handle watchdog specifically - REMOVED (EcoSystem sends, doesn't check per lift this way)
                    # if sys_data.get("xWatchDog", False):
                    #    self.last_watchdog_time[lift_id_loop] = current_time
                    #    watchdog_ok_for_lift = True
                      
                    all_lift_data[lift_id_loop] = lift_data
                # logger.info(f"All lift data: {all_lift_data}") # Debugging line

                if any_critical_read_failed: # This might be triggered by other critical read failures too
                    self._handle_connection_error() # This already updates connection status which resets watchdog display
                    break

                # Update GUI with collected data
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
                await asyncio.sleep(1.0) # Keep this sleep, remove only the commented log
                self._update_connection_status(True)
                # now = time.time() # Removed
                # for lift_id in LIFTS: self.last_watchdog_time[lift_id] = now # Removed
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
            # self.error_controls[lift_id]['clear_error_button'].config(state=tk.DISABLED) # Removed
            self.error_controls[lift_id]['error_status_label'].config(text="PLC Error State: No", foreground="green")

    def _update_connection_status(self, connected):
        self.is_connected = connected
        status_text = "Status: Connected" if connected else "Status: Disconnected"
        status_color = "green" if connected else "red"
        btn_conn_state = tk.DISABLED if connected else tk.NORMAL
        btn_disconn_state = tk.NORMAL if connected else tk.DISABLED

        if self.root.winfo_exists():
            if self.connection_status_label:
                self.connection_status_label.config(text=status_text, foreground=status_color)
            if self.connect_button:
                self.connect_button.config(state=btn_conn_state)
            if self.disconnect_button:
                self.disconnect_button.config(state=btn_disconn_state)
            
            # Update watchdog display on connect/disconnect - REMOVED
            # if connected:
                # When connected, _monitor_plc will update it periodically
                # For the initial state, we can assume it's N/A until the first read
                 # self._update_watchdog_display(False, initial=True) # Show N/A initially
            # else:
                # When disconnected, reset watchdog display
                # self._update_watchdog_display(False, disconnected=True)

            for lift_id in LIFTS:
                self._reset_lift_gui_elements(lift_id)
            # If connected, PLC monitoring will update individual button states via _update_gui_status
        else:
            logger.warning("_update_connection_status called but root window does not exist.")

    def connect_plc(self):
        endpoint = self.endpoint_var.get()
        logger.info(f"Attempting to connect to {endpoint}")
        self.opcua_client.endpoint_url = endpoint
        # Initialize last_watchdog_time for System upon connection attempt - REMOVED
        # self.last_watchdog_time["System"] = time.time() 
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
        """Stuurt een job naar de PLC volgens de interface.txt specificatie"""
        if not self.opcua_client.is_connected:
             messagebox.showwarning("OPC UA", "Not connected to PLC.")
             return
        controls = self.job_controls[lift_id]
        task_type = controls['task_type_var'].get()
        origin = controls['origin_var'].get()
        destination = controls['destination_var'].get()
        logger.info(f"Sending Job to {lift_id} using interface variables: Type={task_type}, Origin={origin}, Dest={destination}")
        
        async def job_write_sequence():
            # Try both formats of interface variables
            # First try direct ElevatorEcoSystAssignment variables
            path_prefix = f"{lift_id}/ElevatorEcoSystAssignment/"
            
            # Write to ElevatorEcoSystAssignment variables (new interface)
            success_type = await self.opcua_client.write_value(f"{path_prefix}iTaskType", task_type, ua.VariantType.Int16)
            success_origin = await self.opcua_client.write_value(f"{path_prefix}iOrigination", origin, ua.VariantType.Int16)  
            success_dest = await self.opcua_client.write_value(f"{path_prefix}iDestination", destination, ua.VariantType.Int16)
            
            # Als de nieuwe interface niet lukt, probeer de oude interface (voor backward compatibility)
            if not all([success_type, success_origin, success_dest]):
                logger.info(f"ElevatorEcoSystAssignment interface failed, trying legacy Eco_i* variables...")
                success_type = await self.opcua_client.write_value(f"{lift_id}/Eco_iTaskType", task_type, ua.VariantType.Int16)
                success_origin = await self.opcua_client.write_value(f"{lift_id}/Eco_iOrigination", origin, ua.VariantType.Int16)  
                success_dest = await self.opcua_client.write_value(f"{lift_id}/Eco_iDestination", destination, ua.VariantType.Int16)
            
            # Controleer of een van beide methoden is geslaagd
            if not (success_type and success_origin and success_dest):
                messagebox.showerror("OPC UA Error", f"Failed to send job to {lift_id}.")
                return
                
            logger.info(f"Job sent successfully to {lift_id}.")
            
        asyncio.create_task(job_write_sequence())

    def acknowledge_job_step(self, lift_id):
        """Stuurt een acknowledge signaal naar de PLC volgens de interface specificatie"""
        if not self.opcua_client.is_connected:
             messagebox.showwarning("OPC UA", "Not connected to PLC.")
             return
        logger.info(f"Sending handshake acknowledge for {lift_id} (xAcknowledgeMovement = True)")
        async def send_acknowledge():
            # Probeer beide paden voor de xAcknowledgeMovement variabele
            # Eerste poging met pad volgens interface.txt
            path1 = f"{lift_id}/ElevatorEcoSystAssignment/xAcknowledgeMovement"
            success1 = await self.opcua_client.write_value(path1, True, ua.VariantType.Boolean)
            
            # Als dat mislukt, probeer alternatief pad
            if not success1:
                path2 = f"{lift_id}.ElevatorEcoSystAssignment.xAcknowledgeMovement"
                success2 = await self.opcua_client.write_value(path2, True, ua.VariantType.Boolean)
                
                # Als ook dat mislukt, probeer legacy pad
                if not success2:
                    path3 = f"{lift_id}/EcoAck_xAcknowldeFromEco"
                    success3 = await self.opcua_client.write_value(path3, True, ua.VariantType.Boolean)
                    
                    if not success3:
                        messagebox.showerror("OPC UA Error", f"Failed to send acknowledge for {lift_id} - all paths failed.")
                        return
            
            logger.info(f"Handshake acknowledge signal sent to {lift_id} via interface variable.")
        
        asyncio.create_task(send_acknowledge())

    def clear_task(self, lift_id):
        """Stuurt een taak-reset commando naar de PLC volgens de interface.txt specificatie"""
        if not self.opcua_client.is_connected:
            messagebox.showwarning("OPC UA", "Not connected to PLC.")
            return
        logger.info(f"Stuur Clear Task via interface variabele iTaskType=0 naar {lift_id}")
        
        async def clear_task_sequence():
            # Probeer meerdere paden naar de iTaskType variabele
            paths_to_try = [
                f"{lift_id}/ElevatorEcoSystAssignment/iTaskType",
                f"{lift_id}.ElevatorEcoSystAssignment.iTaskType",
                f"{lift_id}/Eco_iTaskType"
            ]
            
            success = False
            for path in paths_to_try:
                result = await self.opcua_client.write_value(path, 0, ua.VariantType.Int16)
                if result:
                    logger.info(f"Successfully cleared task via {path}")
                    success = True
                    break
            
            if not success:
                messagebox.showerror("OPC UA Error", f"Failed to clear task for {lift_id} - all paths failed.")
                return
                
            logger.info(f"Clear Task signal sent successfully to {lift_id}.")
            
        asyncio.create_task(clear_task_sequence())

    # def _update_watchdog_display(self, watchdog_ok, initial=False, disconnected=False): # ENTIRE METHOD REMOVED
    #    if not self.watchdog_status_label or not self.root.winfo_exists():
    #        return
    #
    #    if disconnected:
    #        text = "Watchdog: N/A"
    #        color = "grey"
    #    elif initial:
    #        text = "Watchdog: Checking..."
    #        color = "orange"
    #    elif watchdog_ok:
    #        text = "Watchdog: OK"
    #        color = "green"
    #    else:
    #        text = "Watchdog: TIMEOUT"
    #        color = "red"
    #    
    #    self.watchdog_status_label.config(text=text, foreground=color)

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
            logger.exception("Unhandled TclError in __main__:") # Corrected: removed unterminated string
    except Exception as e:
        logger.exception("Unhandled exception in __main__:") # Corrected: removed unterminated string
    finally:
        logger.info("Application exiting __main__.")
