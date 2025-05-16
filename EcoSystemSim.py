import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import asyncio
import logging
import os
import time
import collections # Added import
from asyncua import ua
from opcua_client import OPCUAClient
from lift_visualization import LiftVisualizationManager, LIFTS, LIFT1_ID, LIFT2_ID # Import new manager and constants

# Define Cancel Reason Codes and Texts
CANCEL_REASON_TEXTS = {
    0: "No cancel reason",
    1: "Pickup assignment while tray is on forks",
    2: "Destination out of reach",
    3: "Origin out of reach",
    4: "Destination and origin can’t be zero with a full move operation / Origin can’t be zero with a prepare or move operation",
    5: "Lifts cross each other",
    6: "Invalid assignment"
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
SYS_YELLOW_DIM = '#ADAD00' # Dark Yellow 
SYS_GREEN_BRIGHT = '#00FF00'
SYS_GREEN_DIM = '#006400'  # Dark Green
SYS_BLACK = '#000000' # For border

# Visualisation constants are now in lift_visualization.py
# CANVAS_HEIGHT, CANVAS_WIDTH, etc. are not needed here directly anymore if LiftVisualizationManager handles them internally.

class EcoSystemGUI_DualLift_ST:
    def __init__(self, root):
        self.root = root
        self.root.title("Gibas EcoSystem Simulator (Dual Lift - ST Logic)")
        self.root.geometry("1250x750") # Adjusted for potentially wider right panel and new button
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

        # OPC UA Path Constants
        self.PLC_TO_ECO_BASE = "Di_Call_Blocks/OPC_UA/PlcToEco"
        self.ECO_TO_PLC_BASE = "Di_Call_Blocks/OPC_UA/EcoToPlc"

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

        # Container for the right-side panels (System Stack Light and Auto Mode)
        right_panel_container = ttk.Frame(self.main_controls_frame)
        right_panel_container.pack(side=tk.RIGHT, expand=False, fill="y", padx=(5, 0), pady=0)

        # Frame for the Notebook (Lift1, Lift2 tabs) - Packed to the left of right_panel_container
        notebook_frame = ttk.Frame(self.main_controls_frame)
        notebook_frame.pack(side=tk.LEFT, expand=True, fill="both", padx=(0, 5), pady=0) 
        

        # Create Control Notebook in its dedicated frame
        self._create_control_notebook(notebook_frame) 

        # Frame for the AutoMode panel - REMOVED
        # auto_mode_parent_frame = ttk.LabelFrame(right_panel_container, text="Automatic Mode")
        # auto_mode_parent_frame.pack(side=tk.TOP, expand=False, fill="x", pady=(0,5), padx=2)
        # self._create_auto_mode_controls(auto_mode_parent_frame)


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
        self.connect_button = ttk.Button(conn_frame, text="Connect", command=lambda: asyncio.create_task(self.connect_plc()))
        self.connect_button.grid(row=0, column=2, padx=5, pady=2)
        self.disconnect_button = ttk.Button(conn_frame, text="Disconnect", command=lambda: asyncio.create_task(self.disconnect_plc()), state=tk.DISABLED)
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
            ttk.Label(status_frame, text=f"{var_name}:").grid(row=row_idx, column=col_idx*2, sticky=tk.W, padx=5, pady=2)
            label = ttk.Label(status_frame, text="N/A", width=25, anchor="w")
            label.grid(row=row_idx, column=col_idx*2+1, sticky=tk.W, padx=5, pady=2)
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

    def _create_auto_mode_controls(self, parent_frame):
        """Creates the controls for the Automatic Mode."""
        self.start_auto_mode_button = ttk.Button(parent_frame, text="Start Auto Mode", command=self._start_auto_mode_gui)
        self.start_auto_mode_button.pack(pady=5, padx=5, fill=tk.X)

        self.stop_auto_mode_button = ttk.Button(parent_frame, text="Stop Auto Mode", command=self._stop_auto_mode_gui, state=tk.DISABLED)
        self.stop_auto_mode_button.pack(pady=5, padx=5, fill=tk.X)

        self.auto_mode_status_label = ttk.Label(parent_frame, text="Auto Mode: OFF", foreground="red")
        self.auto_mode_status_label.pack(pady=5, padx=5)

    def _start_auto_mode_gui(self):
        if not self.is_connected:
            messagebox.showwarning("Auto Mode", "Cannot start Auto Mode: Not connected to PLC.")
            return
        if self.auto_mode_controller:
            asyncio.create_task(self.auto_mode_controller.start_auto_mode())
            self._update_auto_mode_gui_status() # Update GUI immediately

    def _stop_auto_mode_gui(self):
        if self.auto_mode_controller:
            asyncio.create_task(self.auto_mode_controller.stop_auto_mode())
            self._update_auto_mode_gui_status() # Update GUI immediately
            

    def _update_auto_mode_gui_status(self):
        if self.auto_mode_controller and self.auto_mode_status_label:
            if self.auto_mode_controller.is_running:
                self.auto_mode_status_label.config(text="Auto Mode: RUNNING", foreground="green")
                if self.start_auto_mode_button: 
                    self.start_auto_mode_button.config(state=tk.DISABLED)
                if self.stop_auto_mode_button: 
                    self.stop_auto_mode_button.config(state=tk.NORMAL)
            else:
                self.auto_mode_status_label.config(text="Auto Mode: OFF", foreground="red")
                if self.start_auto_mode_button: 
                    self.start_auto_mode_button.config(state=tk.NORMAL if self.is_connected else tk.DISABLED)
                if self.stop_auto_mode_button: 
                    self.stop_auto_mode_button.config(state=tk.DISABLED)
        # Call this also when connection status changes
        if not self.is_connected and self.start_auto_mode_button:
             self.start_auto_mode_button.config(state=tk.DISABLED)


    def update_system_stack_light(self, state_key):
        """Updates the global system stack light's colors based on a state key.
        
        Args:
            state_key (str): A string indicating the desired state, e.g., 
                             'off', 'red', 'error', 'yellow', 'warning', 
                             'green', 'ok', 'busy'.
        """
        if not hasattr(self, 'system_stack_light_canvas') or not self.system_stack_light_canvas:
            logger.warning("update_system_stack_light called before canvas initialization.")
            return

        # Default to all dim (which represents 'off')
        red_fill = SYS_RED_DIM
        yellow_fill = SYS_YELLOW_DIM
        green_fill = SYS_GREEN_DIM

        if state_key in ('red', 'error'):
            red_fill = SYS_RED_BRIGHT
        elif state_key in ('yellow', 'warning', 'busy'):
            yellow_fill = SYS_YELLOW_BRIGHT
        elif state_key in ('green', 'ok', 'connected_idle'): # 'connected_idle' for when connected and no issues/activity
            green_fill = SYS_GREEN_BRIGHT
        elif state_key == 'off':
            pass # Defaults are already set for 'off' (all dim)
        else:
            logger.warning(f"Unknown state_key '{state_key}' for update_system_stack_light. Defaulting to 'off'.")
            # Defaults to 'off' (all dim)

        # Check if rect attributes exist before trying to itemconfig
        if hasattr(self, 'system_stack_light_red_rect') and self.system_stack_light_red_rect:
            self.system_stack_light_canvas.itemconfig(self.system_stack_light_red_rect, fill=red_fill)
        if hasattr(self, 'system_stack_light_yellow_rect') and self.system_stack_light_yellow_rect:
            self.system_stack_light_canvas.itemconfig(self.system_stack_light_yellow_rect, fill=yellow_fill)
        if hasattr(self, 'system_stack_light_green_rect') and self.system_stack_light_green_rect:
            self.system_stack_light_canvas.itemconfig(self.system_stack_light_green_rect, fill=green_fill)
        
        logger.debug(f"System stack light set to: {state_key} (R:{red_fill}, Y:{yellow_fill}, G:{green_fill})")

    def _determine_and_update_global_stack_light(self):
        """Determines the global system state and updates the stack light accordingly."""
        if not self.is_connected:
            self.update_system_stack_light('off')
            return

        # Check for errors in any lift
        any_lift_in_error = False
        for lift_id in LIFTS:
            lift_data = self.all_lift_data_cache.get(lift_id, {})
            if self._safe_get_int_from_data(lift_data, "iErrorCode") != 0:
                any_lift_in_error = True
                break
        
        if any_lift_in_error:
            self.update_system_stack_light('error') # Red
            return

        # Check if any lift is busy (e.g., iCycle not 0 or 10, or handshake needed)
        # This is a simplified check; you might need more specific logic
        any_lift_busy = False
        for lift_id in LIFTS:
            lift_data = self.all_lift_data_cache.get(lift_id, {})
            plc_cycle = self._safe_get_int_from_data(lift_data, "iCycle", -1)
            ack_type = self._safe_get_int_from_data(lift_data, "iJobType") 
            if plc_cycle not in [0, 10] and plc_cycle > 0 : # Active cycle
                any_lift_busy = True
                break
            if ack_type > 0: # Awaiting acknowledgement
                any_lift_busy = True
                break
        
        if any_lift_busy:
            self.update_system_stack_light('busy') # Yellow
            return

        # If connected, no errors, and not busy, then system is idle/ok
        self.update_system_stack_light('connected_idle') # Green

    # --- GUI Layout and Element Creation End, Business Logic Methods Below ---

    async def _reset_job_inputs_on_server_for_lift(self, lift_id: str):
        """Resets iTaskType, iOrigination, and iDestination to 0 for a given lift on the OPC UA server."""
        elevator_id_str = self._get_elevator_identifier(lift_id)
        station_idx_for_opc_node = self._get_station_index(lift_id)

        if elevator_id_str is None or station_idx_for_opc_node is None:
            logger.error(f"Cannot determine OPC identifiers for GUI lift ID: {lift_id} in _reset_job_inputs_on_server_for_lift")
            return

        logger.info(f"Resetting job inputs on OPC server for {lift_id} ({elevator_id_str}).")
        try:
            # Path for ElevatorXEcoSystAssignment object
            assignment_base_path = f"{self.ECO_TO_PLC_BASE}/{elevator_id_str}/Elevator{station_idx_for_opc_node + 1}EcoSystAssignment"
            
            # Reset TaskType, Origination, Destination to 0
            # These are the variables the PLC reads for a new job.
            success_type = await self.opcua_client.write_value(f"{assignment_base_path}/iTaskType", 0, ua.VariantType.Int64)
            success_origin = await self.opcua_client.write_value(f"{assignment_base_path}/iOrigination", 0, ua.VariantType.Int64)
            success_dest = await self.opcua_client.write_value(f"{assignment_base_path}/iDestination", 0, ua.VariantType.Int64)

            if success_type and success_origin and success_dest:
                logger.info(f"Successfully reset job inputs (TaskType, Origination, Destination) for {lift_id} on OPC server.")
            else:
                logger.error(f"Failed to fully reset job inputs for {lift_id} on OPC server. Success - Type: {success_type}, Origin: {success_origin}, Dest: {success_dest}")
        except Exception as e:
            logger.exception(f"Error resetting job inputs for {lift_id} on OPC server: {e}")


    def _get_elevator_identifier(self, lift_id_gui: str) -> str:
        """Converts GUI lift ID (e.g., 'Lift1') to PLC elevator ID (e.g., 'Elevator1')."""
        if lift_id_gui == LIFT1_ID: # LIFT1_ID is 'Lift1' from lift_visualization
            return "Elevator1"
        elif lift_id_gui == LIFT2_ID: # LIFT2_ID is 'Lift2' from lift_visualization
            return "Elevator2"
        logger.error(f"Cannot determine elevator identifier for GUI ID: {lift_id_gui}")
        return None

    def _get_station_index(self, lift_id_gui: str) -> int:
        """Converts GUI lift ID to a zero-based numeric index (0 or 1 for station addressing)."""
        # PLC uses 0-based indexing for StationData arrays.
        if lift_id_gui == LIFT1_ID:
            return 0
        elif lift_id_gui == LIFT2_ID:
            return 1
        logger.error(f"Cannot determine station index for GUI ID: {lift_id_gui}")
        return None

    async def _monitor_plc(self):
        """Periodically reads data from the PLC and updates the GUI."""
        # vars_to_read_map: GUI_KEY: (PATH_TYPE, path_template_or_exact_subpath)
        # PATH_TYPE 'StationData': template uses {{idx}}, path is GVL_OPC/PlcToEco/StationData/{{idx}}/TEMPLATE
        # PATH_TYPE 'Elevator': template is direct subpath, path is GVL_OPC/PlcToEco/{{elevator_id_str}}/TEMPLATE
        vars_to_read_map = {
            "iCycle": ("StationData", "iCycle"),
            "iStatus": ("StationData", "iStationStatus"), # Maps to iStationStatus in interface.txt
            "sSeq_Step_comment": ("Elevator", "sSeq_Step_comment"), # Corrected: Maps to ElevatorX/sSeq_Step_comment
            "iJobType": ("StationData", "Handshake/iJobType"),
            "iCancelAssignmentReasonCode": ("StationData", "iCancelAssignment"), # Maps to iCancelAssignment
            "sErrorShortDescription": ("StationData", "sShortAlarmDescription"), # Maps to sShortAlarmDescription
            "sErrorSolution": ("StationData", "sAlarmSolution"), # Maps to sAlarmSolution
            
            # These are expected by GUI but not in interface.txt's StationDataToEco. Reading from ElevatorX path.
            "iElevatorRowLocation": ("Elevator", "iElevatorRowLocation"),
            "xTrayInElevator": ("Elevator", "xTrayInElevator"),
            "iCurrentForkSide": ("Elevator", "iCurrentForkSide"),
            "iErrorCode": ("Elevator", "iErrorCode"), # GUI expects iErrorCode; PLCSim provides this path (No "Error/" subfolder)
            "sErrorMessage": ("Elevator", "sSeq_Step_comment"), # PLCSim provides sSeq_Step_comment under ElevatorX, let's map GUI's sErrorMessage to this for now.
                                                              # interface.txt does not specify sErrorMessage under PlcToEco/ElevatorX
        }

        while self.is_connected:
            try:
                any_update_failed = False
                for lift_id in LIFTS: # LIFT1_ID, LIFT2_ID
                    current_lift_data = self.all_lift_data_cache.get(lift_id, {}).copy() # Work with a copy
                    station_idx_for_opc = self._get_station_index(lift_id) # 0 for Lift1, 1 for Lift2
                    elevator_id_str = self._get_elevator_identifier(lift_id) # "Elevator1", "Elevator2"

                    if station_idx_for_opc is None or elevator_id_str is None:
                        logger.error(f"Cannot determine OPC identifiers for GUI lift ID: {lift_id}")
                        any_update_failed = True
                        continue

                    for gui_key, (path_type, sub_path_template) in vars_to_read_map.items():
                        full_opc_path = ""
                        if path_type == "StationData":
                            # Corrected path construction:
                            full_opc_path = f"{self.PLC_TO_ECO_BASE}/StationData/{station_idx_for_opc}/{sub_path_template}"
                        elif path_type == "Elevator":
                            full_opc_path = f"{self.PLC_TO_ECO_BASE}/{elevator_id_str}/{sub_path_template}"
                        else:
                            logger.warning(f"Unknown path_type: {path_type} for gui_key: {gui_key}")
                            any_update_failed = True
                            continue
                        
                        # logger.debug(f"Attempting to read OPC variable: {full_opc_path} for lift {lift_id}, gui_key {gui_key}")
                        value = await self.opcua_client.read_variable(full_opc_path)
                        if value is not None:
                            current_lift_data[gui_key] = value
                        else:
                            # logger.warning(f"Failed to read {full_opc_path} for {lift_id}. Value is None.")
                            any_update_failed = True
                            current_lift_data[gui_key] = None # Store None to indicate read failure for this var

                    self.all_lift_data_cache[lift_id] = current_lift_data
                    self._update_gui_for_lift(lift_id, current_lift_data)

                self._determine_and_update_global_stack_light()
                if any_update_failed:
                    # logger.debug("One or more OPC reads failed in the cycle.") # Potentially log less frequently
                    pass
                
                await asyncio.sleep(0.25)  # Read interval (e.g., 250ms)
            except asyncio.CancelledError:
                logger.info("PLC monitoring task was cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in PLC monitoring loop: {e}", exc_info=True)
                # Decide if we should stop monitoring or just log and continue
                await asyncio.sleep(2) # Wait a bit longer after a major error
        logger.info("PLC monitoring stopped.")

    def _update_gui_for_lift(self, lift_id: str, lift_data: dict):
        """Updates all relevant GUI elements for a specific lift based on new data."""
        if not self.root.winfo_exists(): return

        # Update status labels (excluding sSeq_Step_comment, iTaskType, iOrigination, iDestination which are handled differently or not read here)
        status_labels_to_update = ["iCycle", "iStatus", "iElevatorRowLocation", "xTrayInElevator", "iCurrentForkSide"]
        for var_name in status_labels_to_update:
            if lift_id in self.status_labels and var_name in self.status_labels[lift_id]:
                value = lift_data.get(var_name)
                display_value = str(value) if value is not None else "ErrorRead"
                self.status_labels[lift_id][var_name].config(text=display_value)
        
        # Update sSeq_Step_comment (Text widget)
        if lift_id in self.status_labels and "sSeq_Step_comment" in self.status_labels[lift_id]:
            comment_widget = self.status_labels[lift_id]["sSeq_Step_comment"]
            new_comment = lift_data.get("sSeq_Step_comment", "ErrorRead")
            # Only update if different to avoid flicker and preserve history if that's desired
            # For now, always update based on deque history
            if new_comment != self.seq_step_history[lift_id][0] if self.seq_step_history[lift_id] else True:
                self.seq_step_history[lift_id].appendleft(new_comment if new_comment is not None else "")
                comment_widget.config(state=tk.NORMAL)
                comment_widget.delete("1.0", tk.END)
                comment_widget.insert("1.0", "\n".join(self.seq_step_history[lift_id]))
                comment_widget.config(state=tk.DISABLED)

        # Update tray presence status (for BringAway job validation and visualization)
        # This read is from xTrayInElevator (PlcToEco)
        tray_present_plc = lift_data.get("xTrayInElevator")
        if tray_present_plc is not None:
            self.lift_tray_status[lift_id] = bool(tray_present_plc)
        # Visualization of tray is handled by lift_vis_manager based on this and other data

        # Visualization update
        # Prepare arguments for update_lift_visual_state
        current_row_for_vis = self._safe_get_int_from_data(lift_data, "iElevatorRowLocation", default=1) # Default to row 1 if not found
        has_tray_for_vis = bool(lift_data.get("xTrayInElevator", False)) # Default to False
        fork_side_for_vis = self._safe_get_int_from_data(lift_data, "iCurrentForkSide", default=0) # Default to MiddenLocation (0)
        is_error_for_vis = self._safe_get_int_from_data(lift_data, "iErrorCode", default=0) != 0
        
        if self.lift_vis_manager:
            try:
                self.lift_vis_manager.update_lift_visual_state(
                    lift_id,
                    current_row_for_vis,
                    has_tray_for_vis,
                    fork_side_for_vis,
                    is_error_for_vis
                )
            except Exception as e:
                logger.error(f"Error calling update_lift_visual_state for {lift_id}: {e}")

        # Update Handshake/Acknowledge section
        if lift_id in self.ack_controls:
            ack_type = self._safe_get_int_from_data(lift_data, "iJobType") # From Handshake/iJobType
            ack_button = self.ack_controls[lift_id]['ack_movement_button']
            ack_label = self.ack_controls[lift_id]['ack_info_label']
            prev_ack_state = getattr(self, f"_prev_ack_state_{lift_id}", None)
            if ack_type > 0:
                if prev_ack_state != True:
                    logger.info(f"Acknowledge requested by PLC for {lift_id} (iJobType={ack_type}).")  # Log when PLC requests ack
                ack_label.config(text=f"PLC Awaiting Ack (Type: {ack_type})", foreground="blue")
                ack_button.config(state=tk.NORMAL)
                setattr(self, f"_prev_ack_state_{lift_id}", True)
            else:
                if prev_ack_state == True:
                    logger.info(f"Acknowledge received by PLC for {lift_id}.")  # Log when PLC has received ack (iJobType returns to 0)
                ack_label.config(text="PLC Awaiting Ack: No", foreground="grey")
                ack_button.config(state=tk.DISABLED)
                setattr(self, f"_prev_ack_state_{lift_id}", False)

        # Update Error Display section
        # Error data is directly from lift_data which contains iErrorCode, sErrorShortDescription etc.
        self._update_error_display(lift_id, lift_data) 

        # Update Cancel Reason Display
        if lift_id in self.status_labels and "iCancelAssignmentReasonCode" in self.status_labels[lift_id]:
            reason_code = self._safe_get_int_from_data(lift_data, "iCancelAssignmentReasonCode")
            self.status_labels[lift_id]["iCancelAssignmentReasonCode"].config(text=str(reason_code))
            reason_text = CANCEL_REASON_TEXTS.get(reason_code, "Unknown or Invalid Code")
            self.status_labels[lift_id]["sCancelAssignmentReasonText"].config(text=reason_text)

        # The Lift Visualization is updated earlier in this method using the correctly prepared arguments.
        # vis_data = {
        #     'iElevatorRowLocation': lift_data.get('iElevatorRowLocation'),
        #     'xTrayInElevator': self.lift_tray_status[lift_id], # Use the locally synced status
        #     'iCurrentForkSide': lift_data.get('iCurrentForkSide'),
        #     'iStatus': lift_data.get('iStatus'),
        #     'iErrorCode': lift_data.get('iErrorCode') # Pass error code for visual indication
        # }
        # seq_comment_for_vis = lift_data.get("sSeq_Step_comment", "")
        # if hasattr(self, 'lift_vis_manager') and self.lift_vis_manager:
        #     self.lift_vis_manager.update_lift_visual_state(lift_id, vis_data, seq_comment_for_vis) # Changed method name
        
        # logger.debug(f"GUI updated for {lift_id} with data: {lift_data}")

    def _safe_get_int_from_data(self, data_dict, key, default=0):
        """Safely gets an integer from a dictionary, handling potential errors."""
        try:
            value = data_dict.get(key)
            if value is None:
                # logger.debug(f"Key '{key}' not found in data. Using default: {default}")
                return default
            return int(value)
        except (ValueError, TypeError):
            # logger.warning(f"Could not convert value for key '{key}' to int. Value: {repr(value)}. Using default: {default}")
            return default

    def _update_error_display(self, lift_id, error_data):
        """Updates the error display section for a lift based on error_data from PLC."""
        if lift_id not in self.error_controls:
            logger.warning(f"_update_error_display: No error controls found for {lift_id}")
            return

        controls = self.error_controls[lift_id]
        error_code = self._safe_get_int_from_data(error_data, "iErrorCode") # Use safe_get

        if error_code != 0:
            controls['error_status_label'].config(text=f"PLC Error State: Yes ({error_code})", foreground="red")
            controls['short_description'].config(text=error_data.get("sErrorShortDescription", "Unknown"), foreground="red")
            
            for widget_key, data_key in [('message', "sErrorMessage"), ('solution', "sErrorSolution")]:
                text_widget = controls.get(widget_key)
                if text_widget:
                    text_widget.config(state=tk.NORMAL)
                    text_widget.delete("1.0", tk.END)
                    text_widget.insert("1.0", error_data.get(data_key, "No details." if widget_key == 'message' else "No solution provided."))
                    text_widget.config(state=tk.DISABLED)
        else:
            controls['error_status_label'].config(text="PLC Error State: No", foreground="green")
            controls['short_description'].config(text="None", foreground="gray")

            for widget_key in ['message', 'solution']:
                text_widget = controls.get(widget_key)
                if text_widget:
                    text_widget.config(state=tk.NORMAL)
                    text_widget.delete("1.0", tk.END)
                    text_widget.insert("1.0", "N/A")
                    text_widget.config(state=tk.DISABLED)
        # logger.debug(f"Error display updated for {lift_id}: Code {error_code}")


    async def connect_plc(self):
        endpoint_url = self.endpoint_var.get()
        logger.info(f"Attempting to connect to PLC at {endpoint_url}...")
        self.connection_status_label.config(text="Status: Connecting...", foreground="orange")
        self.update_system_stack_light('busy') 

        try:
            self.opcua_client.endpoint_url = endpoint_url # Ensure the client uses the potentially updated endpoint URL
            connection_successful = await self.opcua_client.connect() 

            if connection_successful:
                self.is_connected = True
                self.connection_status_label.config(text="Status: Connected", foreground="green")
                self.connect_button.config(state=tk.DISABLED)
                self.disconnect_button.config(state=tk.NORMAL)
                
                for lift_id in LIFTS:
                    if lift_id in self.job_controls:
                        self.job_controls[lift_id]['send_job_button'].config(state=tk.NORMAL)
                        self.job_controls[lift_id]['clear_task_button'].config(state=tk.NORMAL)
                # ack_controls button state is typically managed by _monitor_plc based on PLC state

                # Clear job inputs on OPC server for all lifts to prevent immediate job start
                logger.info("Connection successful. Resetting job inputs on OPC server for all lifts...")
                for lift_id_to_clear in LIFTS:
                    await self._reset_job_inputs_on_server_for_lift(lift_id_to_clear)
                logger.info("Finished resetting job inputs on OPC server for all lifts.")

                logger.info(f"Successfully connected to PLC at {endpoint_url}.")
                self.update_system_stack_light('connected_idle')

                if self.monitoring_task:
                    self.monitoring_task.cancel()
                    try:
                        await self.monitoring_task
                    except asyncio.CancelledError:
                        logger.info("Previous monitoring task cancelled before starting new one.")
                    except Exception as e_task_cancel:
                        logger.error(f"Error awaiting previous monitoring task cancellation: {e_task_cancel}")

                if hasattr(self, '_monitor_plc') and callable(self._monitor_plc):
                    self.monitoring_task = asyncio.create_task(self._monitor_plc())
                    logger.info("PLC monitoring task started.")
                else:
                    logger.error("CRITICAL: _monitor_plc method is not defined. GUI will not update PLC data.")
                    messagebox.showerror("Internal Error", "_monitor_plc method is missing. Cannot monitor PLC.")
                    self.update_system_stack_light('error')
            else:
                # Connection failed as reported by opcua_client.connect()
                self.is_connected = False
                logger.error(f"Failed to connect to PLC at {endpoint_url} (opcua_client.connect returned False).")
                self.connection_status_label.config(text="Status: Connection Failed", foreground="red")
                messagebox.showerror("Connection Error", f"Could not connect to PLC at {endpoint_url}. Check logs.")
                self.update_system_stack_light('error') 
                self.connect_button.config(state=tk.NORMAL)
                self.disconnect_button.config(state=tk.DISABLED)

        except Exception as e:
            self.is_connected = False
            logger.error(f"Failed to connect to PLC: {e}", exc_info=True)
            self.connection_status_label.config(text=f"Status: Error - Check Logs", foreground="red")
            messagebox.showerror("Connection Error", f"Could not connect to PLC: {e}")
            self.update_system_stack_light('error') 
            self.connect_button.config(state=tk.NORMAL)
            self.disconnect_button.config(state=tk.DISABLED)

    async def disconnect_plc(self):
        logger.info("Attempting to disconnect from PLC...")
        self.update_system_stack_light('busy')

        if self.monitoring_task:
            logger.info("Cancelling PLC monitoring task...")
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task 
            except asyncio.CancelledError:
                logger.info("Monitoring task successfully cancelled.")
            except Exception as e:
                logger.error(f"Error during monitoring task cancellation: {e}", exc_info=True)
            self.monitoring_task = None
        
        if self.opcua_client and self.opcua_client.is_connected:
            try:
                await self.opcua_client.disconnect()
                logger.info("Successfully disconnected from PLC.")
            except Exception as e:
                logger.error(f"Error during OPC UA disconnect: {e}", exc_info=True)
        
        self.is_connected = False
        self.connection_status_label.config(text="Status: Disconnected", foreground="red")
        self.connect_button.config(state=tk.NORMAL)
        self.disconnect_button.config(state=tk.DISABLED)
        
        for lift_id in LIFTS:
            if lift_id in self.job_controls:
                self.job_controls[lift_id]['send_job_button'].config(state=tk.DISABLED)
                self.job_controls[lift_id]['clear_task_button'].config(state=tk.DISABLED)
            if lift_id in self.ack_controls:
                 self.ack_controls[lift_id]['ack_movement_button'].config(state=tk.DISABLED)
                 self.ack_controls[lift_id]['ack_info_label'].config(text="PLC Awaiting Ack: No", foreground="grey")

            if lift_id in self.status_labels:
                for var_name, label_widget in self.status_labels[lift_id].items():
                    if var_name == "sSeq_Step_comment" and isinstance(label_widget, tk.Text):
                        label_widget.config(state=tk.NORMAL)
                        label_widget.delete("1.0", tk.END)
                        label_widget.insert("1.0", "N/A")
                        label_widget.config(state=tk.DISABLED)
                    elif isinstance(label_widget, ttk.Label):
                        label_widget.config(text="N/A")
            
            if lift_id in self.error_controls: # Reset error display
                self._update_error_display(lift_id, {"iErrorCode": 0}) # Pass data that signifies no error

            # Reset visualization for the lift if manager supports it
            if hasattr(self.lift_vis_manager, 'reset_lift_visualization'):
                 self.lift_vis_manager.reset_lift_visualization(lift_id)
            elif hasattr(self.lift_vis_manager, 'update_lift_visualization'): # Or update with default/empty data
                 self.lift_vis_manager.update_lift_visualization(lift_id, {}, "N/A")


        self.all_lift_data_cache = {lift_id: {} for lift_id in LIFTS} # Clear cache
        self.update_system_stack_light('off') 
        logger.info("GUI state reset to disconnected.")

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
            messagebox.showwarning("OPC UA", "Not connected to PLC.")
            # Still allow local toggle for visual testing if not connected?
            # For now, let's prevent local toggle if not connected to avoid desync perception.
            return

        elevator_id = self._get_elevator_identifier(lift_id)
        if not elevator_id:
            messagebox.showerror("GUI Error", f"Could not determine elevator identifier for {lift_id}.")
            return

        new_tray_status = not self.lift_tray_status[lift_id] # Toggle local GUI belief
        logger.info(f"Attempting to toggle tray presence for {lift_id} ({elevator_id}) to: {new_tray_status} on PLC and GUI.")

        async def async_write_tray_status():
            # This path writes to the PLC's output variable, effectively overriding the PLC state.
            opc_path = f"{self.PLC_TO_ECO_BASE}/{elevator_id}/xTrayInElevator"
            success = await self.opcua_client.write_value(opc_path, new_tray_status, ua.VariantType.Boolean)
            if success:
                # self.lift_tray_status[lift_id] = new_tray_status # Local state updated by monitor loop from PLC read
                logger.info(f"Successfully wrote {opc_path} = {new_tray_status} to PLC (overriding PLC state). Waiting for monitor to confirm.")
                messagebox.showinfo("Tray Status", f"Tray presence for {lift_id} set to: {new_tray_status} on PLC. GUI will update on next read.")
                # The GUI will visually update once the _monitor_plc loop reads this new value back.
            else:
                logger.error(f"Failed to write {opc_path} = {new_tray_status} to PLC.")
                messagebox.showerror("OPC UA Error", f"Failed to update tray status for {lift_id} on the PLC.")
        
        asyncio.create_task(async_write_tray_status())

    def send_job(self, lift_id: str):
        if not self.is_connected:
            messagebox.showwarning("Send Job", "Not connected to PLC.")
            return

        controls = self.job_controls.get(lift_id)
        if not controls:
            logger.error(f"Job controls not found for {lift_id}")
            return

        try:
            task_type = controls['task_type_var'].get()
            origin_str = controls['origin_entry'].get()
            destination_str = controls['destination_entry'].get()

            origin = int(origin_str) if origin_str else 0
            destination = int(destination_str) if destination_str else 0
        except ValueError:
            messagebox.showerror("Input Error", "Origin and Destination must be valid integers.")
            return
        except Exception as e:
            messagebox.showerror("Input Error", f"Error reading job parameters: {e}")
            return

        elevator_id_str = self._get_elevator_identifier(lift_id)
        station_idx_for_opc_node = self._get_station_index(lift_id) # 0 for Lift1, 1 for Lift2

        if elevator_id_str is None or station_idx_for_opc_node is None:
            logger.error(f"Cannot determine OPC identifiers for GUI lift ID: {lift_id}")
            messagebox.showerror("Internal Error", "Could not determine OPC identifiers.")
            return

        logger.info(f"Sending Job to {lift_id} ({elevator_id_str}): TaskType={task_type}, Origin={origin}, Destination={destination}")

        async def _send_job_async():
            try:
                # Path for ElevatorXEcoSystAssignment object
                assignment_base_path = f"{self.ECO_TO_PLC_BASE}/{elevator_id_str}/Elevator{station_idx_for_opc_node + 1}EcoSystAssignment"
                # Path for variables directly under ElevatorX object
                lift_base_path = f"{self.ECO_TO_PLC_BASE}/{elevator_id_str}"

                # Determine the correct OPC UA name for iCancelAssignment
                cancel_assignment_opc_name = "iCancelAssignment" # Default correct spelling
                if lift_id == LIFT1_ID: # LIFT1_ID is 'Lift1' from lift_visualization
                    cancel_assignment_opc_name = "iCancelAssignent" # Typo for LIFT1_ID

                # Write to ElevatorXEcoSystAssignment
                success_type = await self.opcua_client.write_value(f"{assignment_base_path}/iTaskType", task_type, ua.VariantType.Int64)
                success_origin = await self.opcua_client.write_value(f"{assignment_base_path}/iOrigination", origin, ua.VariantType.Int64)
                success_dest = await self.opcua_client.write_value(f"{assignment_base_path}/iDestination", destination, ua.VariantType.Int64)
                
                # Write directly under ElevatorX
                success_ack = await self.opcua_client.write_value(f"{lift_base_path}/xAcknowledgeMovement", False, ua.VariantType.Boolean)
                success_cancel = await self.opcua_client.write_value(f"{lift_base_path}/{cancel_assignment_opc_name}", 0, ua.VariantType.Int64) # Changed to Int64

                if success_type and success_origin and success_dest and success_ack and success_cancel:
                    logger.info(f"Successfully sent job to {lift_id} ({elevator_id_str}).")
                    # Optionally provide user feedback, though logs are primary for now
                else:
                    logger.error(f"Failed to send job to {lift_id} ({elevator_id_str}). Check OPC UA server/logs.")
                    # messagebox.showerror("OPC UA Error", f"Failed to send job to {lift_id}. Check logs.")
            except Exception as e:
                logger.exception(f"Error sending job to {lift_id}: {e}")
                messagebox.showerror("Job Send Error", f"An error occurred while sending job to {lift_id}: {e}")
        
        asyncio.create_task(_send_job_async())

    def acknowledge_job_step(self, lift_id: str):
        """Acknowledges a job step (movement) for the PLC."""
        if not self.opcua_client.is_connected:
            messagebox.showwarning("OPC UA", "Not connected to PLC.")
            return
        
        elevator_id = self._get_elevator_identifier(lift_id)
        if not elevator_id:
            messagebox.showerror("GUI Error", f"Could not determine elevator identifier for {lift_id}.")
            return

        logger.info(f"Acknowledge requested by user for {lift_id} ({elevator_id}).")  # Log when user requests ack
        async def async_ack():
            # Corrected path: Directly under the ElevatorX object
            path = f"{self.ECO_TO_PLC_BASE}/{elevator_id}/xAcknowledgeMovement"
            success = await self.opcua_client.write_value(path, True, ua.VariantType.Boolean)
            if success:
                logger.info(f"Acknowledge sent to PLC for {lift_id} ({elevator_id}) at path {path}.")  # Log when ack is sent
                # Optionally, reset the GUI ack button or status here, though monitoring loop should update it
            else:
                logger.error(f"Failed to send acknowledge for {lift_id} ({elevator_id}).")
                messagebox.showerror("OPC UA Error", f"Failed to send acknowledge for {lift_id}.")
        asyncio.create_task(async_ack())

    def clear_task(self, lift_id: str):
        """Clears the current task on the PLC by sending TaskType 0."""
        if not self.is_connected:
            messagebox.showwarning("Clear Task", "Not connected to PLC.")
            return

        elevator_id_str = self._get_elevator_identifier(lift_id)
        station_idx_for_opc_node = self._get_station_index(lift_id)

        if elevator_id_str is None or station_idx_for_opc_node is None:
            logger.error(f"Cannot determine OPC identifiers for GUI lift ID: {lift_id} in clear_task")
            messagebox.showerror("Internal Error", "Could not determine OPC identifiers for clear_task.")
            return
            
        logger.info(f"Clearing task for {lift_id} ({elevator_id_str})")

        async def _clear_task_async():
            try:
                # Path for ElevatorXEcoSystAssignment object
                assignment_base_path = f"{self.ECO_TO_PLC_BASE}/{elevator_id_str}/Elevator{station_idx_for_opc_node + 1}EcoSystAssignment"
                # Path for variables directly under ElevatorX object
                lift_base_path = f"{self.ECO_TO_PLC_BASE}/{elevator_id_str}"

                # Determine the correct OPC UA name for iCancelAssignment
                cancel_assignment_opc_name = "iCancelAssignment" # Default correct spelling
                if lift_id == LIFT1_ID: # LIFT1_ID is 'Lift1' from lift_visualization
                    cancel_assignment_opc_name = "iCancelAssignent" # Typo for LIFT1_ID

                # Reset task type in ElevatorXEcoSystAssignment
                success_task_type = await self.opcua_client.write_value(f"{assignment_base_path}/iTaskType", 0, ua.VariantType.Int64)
                
                # Reset cancel assignment directly under ElevatorX
                success_cancel = await self.opcua_client.write_value(f"{lift_base_path}/{cancel_assignment_opc_name}", 0, ua.VariantType.Int64) # Changed to Int64
                
                # Also reset xAcknowledgeMovement if it's part of a "clear" operation's intent
                success_ack = await self.opcua_client.write_value(f"{lift_base_path}/xAcknowledgeMovement", False, ua.VariantType.Boolean)


                if success_task_type and success_cancel and success_ack:
                    logger.info(f"Task cleared successfully for {lift_id} ({elevator_id_str}).")
                else:
                    logger.error(f"Failed to fully clear task for {lift_id} ({elevator_id_str}). Some OPC UA writes might have failed.")
            except Exception as e:
                logger.exception(f"Error clearing task for {lift_id}: {e}")
                messagebox.showerror("Clear Task Error", f"An error occurred while clearing task for {lift_id}: {e}")

        asyncio.create_task(_clear_task_async())

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
        logger.info("Async closing operations started...")
        # if gui.auto_mode_controller and gui.auto_mode_controller.is_running: # REMOVED
        #     logger.info("Auto mode is running, attempting to stop it gracefully...") # REMOVED
        #     await gui.auto_mode_controller.stop_auto_mode() # REMOVED
        #     logger.info("Auto mode stopped.") # REMOVED

        if gui.monitoring_task:
            logger.info("Cancelling PLC monitoring task...")
            gui.monitoring_task.cancel()
            gui.monitoring_task = None
        if gui.opcua_client and gui.opcua_client.is_connected:
            logger.info("OPC UA client is connected, attempting async disconnect.")
            await gui.opcua_client.disconnect() 
        if root.winfo_exists():
            root.destroy()
        logger.info("Window destroyed after potential disconnect and auto mode stop.")

    def on_closing_sync_wrapper(): 
        logger.info("WM_DELETE_WINDOW triggered.")
        if asyncio.get_event_loop().is_running():
            asyncio.create_task(on_closing_async())
        else:
            logger.warning("Event loop not running during on_closing. Attempting to stop auto mode and destroy root directly.")
            # Try to stop auto mode controller if event loop isn't running (best effort)
            if gui.auto_mode_controller and gui.auto_mode_controller.is_running:
                # This is tricky without a running loop for the async stop.
                # For simplicity, we might just log and proceed.
                # Or, if AutoModeController's stop has a synchronous part:
                # gui.auto_mode_controller.is_running = False # Force stop flag
                logger.warning("Cannot guarantee clean async stop of auto_mode_controller without running event loop.")

            if gui.opcua_client and gui.opcua_client.is_connected:
                 logger.warning("OPC UA client connected, but event loop not running for async disconnect.")
                 # asyncio.run(gui.opcua_client.disconnect()) # This might cause issues if called from sync context that's part of an outer async context
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
        # Ensure auto mode is stopped
        # if gui.auto_mode_controller and gui.auto_mode_controller.is_running:
        #     logger.info("Main finally: Auto mode is running, ensuring it's stopped.")
        #     try:
        #         loop = asyncio.get_event_loop()
        #         if loop.is_running() and not loop.is_closed():
        #             stop_auto_task = asyncio.create_task(gui.auto_mode_controller.stop_auto_mode())
        #             # Give it a moment to complete, but don't block indefinitely
        #             await asyncio.wait_for(stop_auto_task, timeout=2.0) 
        #             logger.info("Main finally: Auto mode stop task completed or timed out.")
        #         else:
        #             logger.warning("Main finally: Event loop not running or closed, cannot stop auto mode cleanly.")
        #     except asyncio.TimeoutError:
        #         logger.warning("Main finally: Timeout waiting for auto mode to stop.")
        #     except Exception as e_auto_stop:
        #         logger.error(f"Main finally: Error trying to stop auto mode: {e_auto_stop}")
        
        if gui.opcua_client and gui.opcua_client.is_connected:
            logger.info("Main finally: OPC UA client is connected, ensuring disconnection.")
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
