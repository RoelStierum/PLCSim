import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import asyncio
import logging
import os
import time
import collections # Added import
from asyncua import ua
from opcua_client import OPCUAClient
from lift_visualization import LiftVisualizationManager, LIFT1_ID, LIFT2_ID, LIFTS # Import new manager and constants

# Define Cancel Reason Codes and Texts
CANCEL_REASON_TEXTS = {
    0: "No cancel reason",
    1: "Pickup assignment while tray is on forks",
    2: "Destination out of reach",
    3: "Origin out of reach",
    4: "Destination and origin can’t be zero with a full move operation / Origin can’t be zero with a prepare or move operation",
    5: "Lifts cross each other",
    6: "Invalid assignment"
    # Add other reasons as they are defined in PLCSim or interface.txt
}

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

# Define colors for the system stack light
SYS_RED_BRIGHT = '#FF0000'
SYS_RED_DIM = '#8B0000'  # Dark Red
SYS_YELLOW_BRIGHT = '#FFFF00'
SYS_YELLOW_DIM = '#ADAD00' # Dark Yellow (adjusted for better visibility than #8B8B00)
SYS_GREEN_BRIGHT = '#00FF00'
SYS_GREEN_DIM = '#006400'  # Dark Green
SYS_BLACK = '#000000' # For border

# Visualisation constants are now in lift_visualization.py
# CANVAS_HEIGHT, CANVAS_WIDTH, etc. are not needed here directly anymore if LiftVisualizationManager handles them internally.

class EcoSystemGUI_DualLift_ST:
    def __init__(self, root):
        self.root = root
        self.root.title("Gibas EcoSystem Simulator (Dual Lift - ST Logic)")
        self.root.geometry("1100x750") # Adjusted for potentially wider right panel and new button
        self.opcua_client = OPCUAClient(PLC_ENDPOINT, PLC_NS_URI)
        self.is_connected = False
        self.monitoring_task = None
        self.all_lift_data_cache = {lift_id: {} for lift_id in LIFTS} # Cache for error states

        self.lift_frames = {}
        self.status_labels = {}
        self.job_controls = {}
        self.ack_controls = {}
        self.error_controls = {}
        self.lift_tray_status = {lift_id: False for lift_id in LIFTS} # Initialize tray status
        self.seq_step_history = {lift_id: collections.deque(maxlen=5) for lift_id in LIFTS} # Changed maxlen to 5

        # For system stack light
        self.system_stack_light_canvas = None
        self.system_stack_light_red_rect = None
        self.system_stack_light_yellow_rect = None
        self.system_stack_light_green_rect = None

        self._setup_gui_layout()
        
        # Initialize LiftVisualizationManager after canvas is created in _setup_gui_layout
        self.lift_vis_manager = LiftVisualizationManager(self.root, self.shared_canvas, LIFTS)
        
        self.update_system_stack_light('off') # Initial state

    def _setup_gui_layout(self):
        """Creates the main GUI layout, frames, and widgets."""
        self._create_connection_frame()

        # Main content frame that will hold visualization and controls
        content_frame = ttk.Frame(self.root)
        content_frame.pack(expand=True, fill="both", padx=10, pady=5)

        self._create_visualization_frame(content_frame) # Visualization on the left

        # --- Right Panel for Controls --- 
        self.main_controls_frame = ttk.Frame(content_frame)
        self.main_controls_frame.pack(side=tk.RIGHT, expand=True, fill="both", padx=5, pady=0)

        # Container for the right-side panels (System Stack Light)
        right_panel_container = ttk.Frame(self.main_controls_frame)
        right_panel_container.pack(side=tk.RIGHT, expand=False, fill="y", padx=(5, 0), pady=0)

        # Frame for the Notebook (Lift1, Lift2 tabs) - Packed to the left of right_panel_container
        notebook_frame = ttk.Frame(self.main_controls_frame)
        notebook_frame.pack(side=tk.LEFT, expand=True, fill="both", padx=(0, 5), pady=0) 
        
        # Create Control Notebook in its dedicated frame
        self._create_control_notebook(notebook_frame) 

        # Frame for the AutoMode panel (REMOVED)
        # auto_mode_parent_frame = ttk.LabelFrame(right_panel_container, text="Automatic Mode")
        # auto_mode_parent_frame.pack(side=tk.TOP, expand=False, fill="x", pady=(0,5), padx=2)

        # Initialize Auto Mode Manager and add its UI to its dedicated frame (REMOVED)
        # self.auto_mode_manager = add_auto_mode_to_gui(self, auto_mode_parent_frame, self.auto_mode_var)

        # Frame for the System Stack Light (inside right_panel_container, below auto_mode_parent_frame)
        system_stack_light_frame = ttk.LabelFrame(right_panel_container, text="System Status")
        system_stack_light_frame.pack(side=tk.TOP, expand=False, fill="x", pady=(5,0), padx=2)
        system_stack_light_frame.grid_columnconfigure(0, weight=1) # Allow canvas to center if packed

        self.system_stack_light_canvas = tk.Canvas(system_stack_light_frame, width=50, height=70, bg=self.root.cget('bg')) # Use root bg
        self.system_stack_light_canvas.pack(pady=5)

        rect_width = 30
        rect_height = 20
        border_width = 1
        canvas_center_x = 50 / 2

        # Red light (top)
        self.system_stack_light_red_rect = self.system_stack_light_canvas.create_rectangle(
            canvas_center_x - rect_width/2, 5, canvas_center_x + rect_width/2, 5 + rect_height,
            fill=SYS_RED_DIM, outline=SYS_BLACK, width=border_width
        )
        # Yellow light (middle)
        self.system_stack_light_yellow_rect = self.system_stack_light_canvas.create_rectangle(
            canvas_center_x - rect_width/2, 5 + rect_height, canvas_center_x + rect_width/2, 5 + 2 * rect_height,
            fill=SYS_YELLOW_DIM, outline=SYS_BLACK, width=border_width
        )
        # Green light (bottom)
        self.system_stack_light_green_rect = self.system_stack_light_canvas.create_rectangle(
            canvas_center_x - rect_width/2, 5 + 2 * rect_height, canvas_center_x + rect_width/2, 5 + 3 * rect_height,
            fill=SYS_GREEN_DIM, outline=SYS_BLACK, width=border_width
        )

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
        # Set expand to False for vis_frame so it only takes the width of the canvas
        vis_frame.pack(side=tk.LEFT, fill=tk.Y, expand=False, padx=10, pady=5) # Changed fill to tk.Y and expand to False
        from lift_visualization import CANVAS_WIDTH, CANVAS_HEIGHT # Import canvas dimensions
        self.shared_canvas = tk.Canvas(vis_frame, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg='#EAEAEA')
        # Canvas should fill the space given to vis_frame (which is now minimal)
        self.shared_canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _create_control_notebook(self, parent_frame):
        """Creates the notebook with tabs for each lift's controls."""
        self.notebook = ttk.Notebook(parent_frame)
        self.notebook.pack(expand=True, fill="both", padx=0, pady=5) 

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
        self._create_error_display_section(content_frame, lift_id)
        self._create_cancel_reason_section(content_frame, lift_id) # Added call

    def _create_status_section(self, parent_frame, lift_id):
        """Creates the status display section for a lift."""
        status_vars_to_display = [
            "iCycle", "iStatus", "iElevatorRowLocation", 
            "xTrayInElevator", "iCurrentForkSide",
            # Use base names, assuming they are read from ElevatorEcoSystAssignment
            "iTaskType", "iOrigination", "iDestination" 
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
        seq_step_text = tk.Text(step_frame, height=5, width=90, borderwidth=1, relief="groove") # Changed height from 3 to 5
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
        
        # Radiobuttons for Task Type
        task_types_frame = ttk.Frame(job_frame)
        task_types_frame.grid(row=0, column=1, columnspan=4, sticky=tk.W) # Span more columns

        ttk.Radiobutton(task_types_frame, text="1: Full", variable=controls['task_type_var'], value=1, command=lambda l=lift_id: self._on_task_type_change(l)).grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(task_types_frame, text="2: MoveTo", variable=controls['task_type_var'], value=2, command=lambda l=lift_id: self._on_task_type_change(l)).grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Radiobutton(task_types_frame, text="3: PreparePickUp", variable=controls['task_type_var'], value=3, command=lambda l=lift_id: self._on_task_type_change(l)).grid(row=0, column=2, sticky=tk.W, padx=5)
        ttk.Radiobutton(task_types_frame, text="4: BringAway", variable=controls['task_type_var'], value=4, command=lambda l=lift_id: self._on_task_type_change(l)).grid(row=0, column=3, sticky=tk.W, padx=5)
        
        ttk.Label(job_frame, text="Task Type:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        
        # Standaardwaarden voor origin en destination - gebruik geldige standaardwaarden
        controls['origin_var'] = tk.IntVar(value=5)
        ttk.Label(job_frame, text="Origin:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        controls['origin_entry'] = ttk.Entry(job_frame, textvariable=controls['origin_var'], width=10)
        controls['origin_entry'].grid(row=1, column=1, sticky=tk.W, padx=5)
        
        controls['destination_var'] = tk.IntVar(value=90)
        ttk.Label(job_frame, text="Destination:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        controls['destination_entry'] = ttk.Entry(job_frame, textvariable=controls['destination_var'], width=10)
        controls['destination_entry'].grid(row=2, column=1, sticky=tk.W, padx=5)

        # Toggle Tray Button
        controls['toggle_tray_button'] = ttk.Button(job_frame, text="Toggle Tray", command=lambda l=lift_id: self._toggle_tray_presence(l))
        controls['toggle_tray_button'].grid(row=1, column=2, padx=10, sticky=tk.W) # Positioned next to Origin
        
        controls['send_job_button'] = ttk.Button(job_frame, text="Send Job Request", command=lambda l=lift_id: self.send_job(l), state=tk.DISABLED)
        controls['send_job_button'].grid(row=3, column=0, columnspan=2, pady=10, sticky=tk.W)
        controls['clear_task_button'] = ttk.Button(job_frame, text="Clear Task (Reset PLC)", command=lambda l=lift_id: self.clear_task(l), state=tk.DISABLED)
        controls['clear_task_button'].grid(row=3, column=2, columnspan=2, pady=10, sticky=tk.W) # Adjusted column span and sticky
        self.job_controls[lift_id] = controls
        self._on_task_type_change(lift_id) # Call once to set initial state of origin entry

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

    def _create_cancel_reason_section(self, parent_frame, lift_id):
        """Creates the cancel reason display section for a lift."""
        cancel_frame = ttk.LabelFrame(parent_frame, text=f"{lift_id} Job Cancel Reason", padding=10)
        cancel_frame.pack(fill=tk.X, pady=5)
        
        if lift_id not in self.status_labels: # Ensure the main dict exists
            self.status_labels[lift_id] = {}

        ttk.Label(cancel_frame, text="Cancel Code:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        code_label = ttk.Label(cancel_frame, text="N/A", width=10)
        code_label.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        self.status_labels[lift_id]["iCancelAssignmentReasonCode"] = code_label

        ttk.Label(cancel_frame, text="Reason Text:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        text_label = ttk.Label(cancel_frame, text="N/A", width=40, wraplength=250, justify=tk.LEFT) # Added wraplength and justify
        text_label.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        self.status_labels[lift_id]["sCancelAssignmentReasonText"] = text_label

    # --- GUI Layout and Element Creation End, Business Logic Methods Below ---

    def _on_task_type_change(self, lift_id):
        """Callback when the task type radio button changes."""
        if lift_id not in self.job_controls: return
        task_type = self.job_controls[lift_id]['task_type_var'].get()
        origin_entry = self.job_controls[lift_id].get('origin_entry')
        destination_entry = self.job_controls[lift_id].get('destination_entry')

        if origin_entry:
            if task_type == 4: # Bring Away
                origin_entry.config(state=tk.DISABLED)
            else:
                origin_entry.config(state=tk.NORMAL)
        
        if destination_entry:
            if task_type == 3: # Prepare PickUp
                destination_entry.config(state=tk.DISABLED)
            elif task_type == 2: # MoveToAssignment
                destination_entry.config(state=tk.DISABLED) # Disable Destination for MoveTo
            else:
                destination_entry.config(state=tk.NORMAL)

    def _toggle_tray_presence(self, lift_id):
        """Toggles the simulated tray presence for a lift, updates visualization, and writes to PLC."""
        if not self.is_connected:
            messagebox.showwarning("OPC UA", "Not connected to PLC. Cannot update PLC tray status.")
            # Still allow local toggle for visual testing if not connected?
            # For now, let's prevent local toggle if not connected to avoid desync perception.
            return

        new_tray_status = not self.lift_tray_status[lift_id]
        logger.info(f"Attempting to toggle tray presence for {lift_id} to: {new_tray_status} on PLC and GUI.")

        async def async_write_tray_status():
            opc_path = f"{lift_id}/xTrayInElevator"
            success = await self.opcua_client.write_value(opc_path, new_tray_status, ua.VariantType.Boolean)
            if success:
                self.lift_tray_status[lift_id] = new_tray_status # Update local state only if PLC write succeeds
                logger.info(f"Successfully wrote {opc_path} = {new_tray_status} to PLC.")
                messagebox.showinfo("Tray Status", f"Tray presence for {lift_id} set to: {new_tray_status} on PLC and GUI.")
                
                # Update visualization immediately after successful PLC write and local state update
                current_plc_data = self.all_lift_data_cache.get(lift_id, {})
                current_row = self._safe_get_int_from_data(current_plc_data, "iElevatorRowLocation", self.lift_vis_manager.last_position.get(lift_id, 1))
                fork_side = self._safe_get_int_from_data(current_plc_data, "iCurrentForkSide", 0)
                is_error = self._safe_get_int_from_data(current_plc_data, "iErrorCode") != 0
                self.lift_vis_manager.update_lift_visual_state(lift_id, current_row, self.lift_tray_status[lift_id], fork_side, is_error)
            else:
                logger.error(f"Failed to write {opc_path} = {new_tray_status} to PLC.")
                messagebox.showerror("OPC UA Error", f"Failed to update tray status for {lift_id} on the PLC.")
        
        asyncio.create_task(async_write_tray_status())

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
            # Special handling for the reason text label, as its content is derived,
            # not directly from plc_data[name]. It's updated when iCancelAssignmentReasonCode is processed.
            if name == "sCancelAssignmentReasonText":
                continue  # Skip generic update for this label, it's handled below

            value = plc_data.get(name, "N/A")
            
            if name == "sSeq_Step_comment":
                new_comment = str(value).strip()
                if not new_comment: new_comment = "(No comment)"
                
                # Update history only if the new comment is different from the last one
                if not self.seq_step_history[lift_id] or self.seq_step_history[lift_id][-1] != new_comment:
                    self.seq_step_history[lift_id].append(new_comment)
                
                # Format display text from history
                display_text = "\n".join(list(self.seq_step_history[lift_id]))
                value = display_text # This will be written to the Text widget

            elif name.startswith("s") and isinstance(value, bytes): 
                value = value.decode('utf-8', 'ignore').strip()
            
            if isinstance(value, float): value = f"{value:.2f}"
            
            # Handle cancel reason code and update corresponding text label
            if name == "iCancelAssignmentReasonCode":
                # The actual data from PLC is stored under "iCancelAssignmentReason" in plc_data
                reason_code = self._safe_get_int_from_data(plc_data, "iCancelAssignmentReason", 0)
                value = reason_code # This value (the code) will be set to the iCancelAssignmentReasonCode label
                
                # Update the separate sCancelAssignmentReasonText label
                text_label_widget_for_reason = self.status_labels[lift_id].get("sCancelAssignmentReasonText")
                if text_label_widget_for_reason:
                    reason_text = CANCEL_REASON_TEXTS.get(reason_code, f"Unknown reason code ({reason_code})")
                    text_label_widget_for_reason.config(text=reason_text)

            if isinstance(label_widget, tk.Text):
                label_widget.config(state=tk.NORMAL)
                label_widget.delete("1.0", tk.END)
                label_widget.insert("1.0", str(value)) # Value is now potentially multi-line for sSeq_Step_comment
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
        # error_button_state = tk.DISABLED # Removed
        error_label_color = "green"
        if is_error and self.is_connected:
            error_info_text = f"PLC Error State: YES (Code: {error_code})"
            # error_button_state = tk.NORMAL # Removed
            error_label_color = "red"
            
            # Vul de error details secties
            if lift_id in self.error_controls:
                # Haal de foutbeschrijvingen op uit de PLC data
                short_desc = plc_data.get("sShortAlarmDescription", "")
                if isinstance(short_desc, bytes):
                    short_desc = short_desc.decode('utf-8', 'ignore').strip()
                
                error_msg = plc_data.get("sAlarmMessage", "")
                if isinstance(error_msg, bytes):
                    error_msg = error_msg.decode('utf-8', 'ignore').strip()
                    
                solution = plc_data.get("sAlarmSolution", "")
                if isinstance(solution, bytes):
                    solution = solution.decode('utf-8', 'ignore').strip()
                
                # Vul de error details widgets
                self.error_controls[lift_id]['short_description'].config(text=short_desc or "Unknown error", foreground="red")
                
                # Bijwerken van de message textbox
                msg_widget = self.error_controls[lift_id]['message']
                msg_widget.config(state=tk.NORMAL)
                msg_widget.delete("1.0", tk.END)
                msg_widget.insert("1.0", error_msg or "Geen details beschikbaar")
                msg_widget.config(state=tk.DISABLED)
                
                # Bijwerken van de solution textbox
                sol_widget = self.error_controls[lift_id]['solution']
                sol_widget.config(state=tk.NORMAL)
                sol_widget.delete("1.0", tk.END)
                sol_widget.insert("1.0", solution or "Geen oplossing beschikbaar")
                sol_widget.config(state=tk.DISABLED)
        else:
            # Reset error details als er geen fout is
            if lift_id in self.error_controls:
                self.error_controls[lift_id]['short_description'].config(text="None", foreground="gray")
                
                # Reset message textbox
                msg_widget = self.error_controls[lift_id]['message']
                msg_widget.config(state=tk.NORMAL)
                msg_widget.delete("1.0", tk.END)
                msg_widget.insert("1.0", "")
                msg_widget.config(state=tk.DISABLED)
                
                # Reset solution textbox
                sol_widget = self.error_controls[lift_id]['solution']
                sol_widget.config(state=tk.NORMAL)
                sol_widget.delete("1.0", tk.END)
                sol_widget.insert("1.0", "")
                sol_widget.config(state=tk.DISABLED)
                
        if lift_id in self.error_controls:
             self.error_controls[lift_id]['error_status_label'].config(text=error_info_text, foreground=error_label_color)
             # self.error_controls[lift_id]['clear_error_button'].config(state=error_button_state) # Removed

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

        # Cache the latest data for this lift
        self.all_lift_data_cache[lift_id] = plc_data

        self._update_lift_text_labels(lift_id, plc_data)
        self._update_lift_button_states(lift_id, plc_data)

        # Delegate Visualization Update to LiftVisualizationManager
        current_row = self._safe_get_int_from_data(plc_data, "iElevatorRowLocation")
        has_tray = plc_data.get("xTrayInElevator", False)
        fork_side = self._safe_get_int_from_data(plc_data, "iCurrentForkSide", 0)
        is_error = self._safe_get_int_from_data(plc_data, "iErrorCode") != 0
        plc_cycle = self._safe_get_int_from_data(plc_data, "iCycle", -1) # Get iCycle

        # Update visualization
        # stoplight_status = "off" # This local variable is no longer used for individual lift viz
        # if is_error:
            # stoplight_status = "red"
        # elif has_tray:
            # stoplight_status = "yellow" # Or another color indicating loaded
        # else: # Potentially green if idle and no error, or just off
            # stoplight_status = "green"

        self.lift_vis_manager.update_lift_visual_state(lift_id, current_row, has_tray, fork_side, is_error)
        # Global stack light is updated in _monitor_plc after all data is processed

    async def _monitor_plc(self):
        logger.info("Starting Dual Lift PLC monitoring task (ST Logic).")
        
        interface_vars = [
            "iAmountOfSations", 
            "iMainStatus",
            "iCancelAssignmentReason" 
        ]
        station_vars = [
            "StationData.iCycle",
            "StationData.iStationStatus", 
            "StationData.sStationStateDescription",
            "StationData.sShortAlarmDescription", 
            "StationData.sAlarmSolution",
            "StationData.Handshake.iRowNr",
            "StationData.Handshake.iJobType"
        ]
        internal_vars = [
            "iElevatorRowLocation",
            "xTrayInElevator",
            "iCurrentForkSide",
            "iErrorCode",
            "sSeq_Step_comment"
        ]
        eco_assignment_vars = [
            "ElevatorEcoSystAssignment.iTaskType",
            "ElevatorEcoSystAssignment.iOrigination",
            "ElevatorEcoSystAssignment.iDestination",
            "ElevatorEcoSystAssignment.xAcknowledgeMovement",
            "ElevatorEcoSystAssignment.iCancelAssignmentReason"
        ]
        active_job_vars_to_read_and_map = {
            "ActiveElevatorAssignment_iTaskType": "iTaskType",
            "ActiveElevatorAssignment_iOrigination": "iOrigination",
            "ActiveElevatorAssignment_iDestination": "iDestination"
        } 

        logger.info(f"Monitoring system interface variables: {interface_vars}")
        logger.info(f"Monitoring station data variables: {station_vars}")
        logger.info(f"Monitoring EcoSystem assignment variables: {eco_assignment_vars}")
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
                any_critical_read_failed = False # Initialize here
                sys_data = {} # Initialize here

                # Send EcoSystem watchdog signal to PLC
                ecosystem_watchdog_sent_ok = await self.opcua_client.write_value("xWatchDog", True, ua.VariantType.Boolean)
                if not ecosystem_watchdog_sent_ok:
                    logger.warning("Failed to send EcoSystem watchdog signal (xWatchDog) to PLC. Assuming connection issue.")
                    any_critical_read_failed = True 
                    # self._handle_connection_error() # More direct handling
                    # break # Exit monitoring loop on critical failure

                # Read system variables first
                for var_name in interface_vars:
                    value = await self.opcua_client.read_value(var_name)
                    if value is not None:
                        sys_data[var_name] = value
                    elif var_name in ["iMainStatus"]: # Example of a critical variable to check for None
                        logger.error(f"CRITICAL READ FAILED for system variable {var_name}. Aborting monitor for this cycle.")
                        any_critical_read_failed = True
                        break # Break from reading system vars
                
                if any_critical_read_failed:
                    self._handle_connection_error()
                    break

                current_cycle_all_lift_data = {}

                # Read station data for each lift
                for lift_id_loop in LIFTS:
                    lift_data = {} 
                    for key, value in sys_data.items(): 
                        lift_data[key] = value
                    
                    # Read StationData variables
                    for var_name_full_path in station_vars:
                        base_name = var_name_full_path.split('.')[-1]
                        object_structure = var_name_full_path.rsplit('.', 1)[0]
                        path_to_try = f"{lift_id_loop}/{object_structure.replace('.', '/')}/{base_name}"
                        value = await self.opcua_client.read_value(path_to_try)
                        if value is not None: lift_data[base_name] = value
                        else: logger.warning(f"Failed to read {var_name_full_path} for {lift_id_loop} path: {path_to_try}")

                    # Read ElevatorEcoSystAssignment variables
                    for var_name_full_path in eco_assignment_vars:
                        base_name = var_name_full_path.split('.')[-1]
                        path_to_try = f"{lift_id_loop}/ElevatorEcoSystAssignment/{base_name}"
                        value = await self.opcua_client.read_value(path_to_try)
                        if value is not None: lift_data[base_name] = value
                        else: logger.warning(f"Failed to read {var_name_full_path} for {lift_id_loop} path: {path_to_try}")
                    
                    # Read PLC's actual active job parameters
                    for plc_var_name, display_key in active_job_vars_to_read_and_map.items():
                        path_to_try = f"{lift_id_loop}/{plc_var_name}"
                        value = await self.opcua_client.read_value(path_to_try)
                        if value is not None: lift_data[display_key] = value
                        else:
                            logger.warning(f"Failed to read PLC active job parameter {plc_var_name} for {lift_id_loop} (path: {path_to_try}).")
                            if display_key not in lift_data or lift_data[display_key] is None: lift_data[display_key] = 0
                    
                    # Read other internal variables
                    for var_name in internal_vars:
                        paths_to_try = [f"{lift_id_loop}/{var_name}", f"{lift_id_loop}.{var_name}"]
                        value = None
                        for path in paths_to_try:
                            value = await self.opcua_client.read_value(path)
                            if value is not None: break
                        if value is not None: lift_data[var_name] = value
                        # else: logger.warning(f"Failed to read internal var {var_name} for {lift_id_loop}") # Optional: log if needed

                    current_cycle_all_lift_data[lift_id_loop] = lift_data
                    self.all_lift_data_cache[lift_id_loop] = lift_data # Update cache for _determine_and_update_global_stack_light

                # Update GUI with collected data
                for lift_id_gui, data_gui in current_cycle_all_lift_data.items():
                    if data_gui: 
                        self.root.after(0, self._update_gui_status, lift_id_gui, data_gui)
                
                # Determine and update global stack light AFTER all lift data for the cycle is processed
                self.root.after(0, self._determine_and_update_global_stack_light)
                
                await asyncio.sleep(0.2) # OPCUA read interval
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
                # await asyncio.sleep(1.0) # Keep this sleep, remove only the commented log # Original comment
                self._update_connection_status(True) # This will trigger stack light update
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
            
            for lift_id in LIFTS:
                self._reset_lift_gui_elements(lift_id) # Resets errors, so stack light needs update
            
            self._determine_and_update_global_stack_light() # Update stack light based on new connection status

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

        if task_type == 4: # Bring Away
            if not self.lift_tray_status[lift_id]:
                messagebox.showwarning("Job Error", f"Cannot send 'Bring Away' for {lift_id}: No tray present (simulated). Use 'Toggle Tray' button first.")
                return
            logger.info(f"Sending Job to {lift_id}: TaskType={task_type} (Bring Away), Destination={destination}. Origin is current lift position with tray.")
            # For BringAway, PLC uses its current position as origin. We only send TaskType and Destination.
            # Origin variable is ignored by PLC for TaskType 4 based on PLC logic.
        elif task_type == 3: # Prepare PickUp
            logger.info(f"Sending Job to {lift_id}: TaskType={task_type} (Prepare PickUp), Origin={origin}. Destination is ignored.")
            # For PreparePickUp, PLC uses its current position as destination. We only send TaskType and Origin.
            # Destination variable is ignored by PLC for TaskType 3.
        else:
            logger.info(f"Sending Job to {lift_id} using interface variables: Type={task_type}, Origin={origin}, Dest={destination}")
        
        async def job_write_sequence():
            path_prefix = f"{lift_id}/ElevatorEcoSystAssignment/"
            
            success_type = await self.opcua_client.write_value(f"{path_prefix}iTaskType", task_type, ua.VariantType.Int64)
            success_origin = True # Assume success if not applicable
            success_dest = True   # Assume success if not applicable

            if task_type == 1: # FullAssignment
                success_origin = await self.opcua_client.write_value(f"{path_prefix}iOrigination", origin, ua.VariantType.Int64)
                success_dest = await self.opcua_client.write_value(f"{path_prefix}iDestination", destination, ua.VariantType.Int64)
            elif task_type == 2: # MoveToAssignment
                success_dest = await self.opcua_client.write_value(f"{path_prefix}iOrigination", origin, ua.VariantType.Int64)
                # Origin is not used by PLC for MoveTo
            elif task_type == 3: # PreparePickUp
                success_origin = await self.opcua_client.write_value(f"{path_prefix}iOrigination", origin, ua.VariantType.Int64)
                # Destination is not used by PLC for PreparePickUp
            elif task_type == 4: # BringAway
                success_dest = await self.opcua_client.write_value(f"{path_prefix}iDestination", destination, ua.VariantType.Int64)
                # Origin is not used by PLC for BringAway (uses current pos)

            if not all([success_type, success_origin, success_dest]):
                # Fallback to legacy only if primary fails and it's not a task type (3 or 4) that has specific field handling
                if task_type <= 2: 
                    logger.info(f"ElevatorEcoSystAssignment interface failed for TaskType {task_type}, trying legacy Eco_i* variables...")
                    # Ensure legacy variables are only attempted for task types that used them fully
                    legacy_success_type = await self.opcua_client.write_value(f"{lift_id}/Eco_iTaskType", task_type, ua.VariantType.Int16)
                    legacy_success_origin = await self.opcua_client.write_value(f"{lift_id}/Eco_iOrigination", origin, ua.VariantType.Int16)
                    legacy_success_dest = await self.opcua_client.write_value(f"{lift_id}/Eco_iDestination", destination, ua.VariantType.Int16)
                    if not (legacy_success_type and legacy_success_origin and legacy_success_dest):
                        messagebox.showerror("OPC UA Error", f"Failed to send job to {lift_id} using both new and legacy interfaces.")
                        return
                else: # For task types 3 and 4, if the new interface fails, it's a direct error.
                    messagebox.showerror("OPC UA Error", f"Failed to send job (Type {task_type}) to {lift_id} using ElevatorEcoSystAssignment interface.")
                    return
            
            logger.info(f"Job (Type {task_type}) sent successfully to {lift_id}.")
            
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
                    path3 = f"{lift_id}/EcoAck_xAknowledeFromEco"
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
                # Use Int64 for task type reset as well, to be consistent
                result = await self.opcua_client.write_value(path, 0, ua.VariantType.Int64)
                if result:
                    logger.info(f"Successfully cleared task via {path}")
                    success = True
                    break
            
            if not success:
                messagebox.showerror("OPC UA Error", f"Failed to clear task for {lift_id} - all paths failed.")
                return
                
            logger.info(f"Clear Task signal sent successfully to {lift_id}.")
            
        asyncio.create_task(clear_task_sequence())

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
