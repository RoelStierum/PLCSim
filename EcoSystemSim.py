import asyncio
import logging
from asyncua import Client, ua
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import time
import os # Import os module for path handling

# Zorg dat de logs map bestaat
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Visualisatie constanten
CANVAS_HEIGHT = 500  # Verhoogd van 300 naar 500
CANVAS_WIDTH = 120   # Verhoogd van 50 naar 120
MAX_ROWS = 100       # Aantal rijen blijft hetzelfde
LIFT_WIDTH = 60      # Verhoogd van 30 naar 60
LIFT_HEIGHT_RATIO = 0.05  # Hoogte ratio blijft hetzelfde

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
LIFT1_ID = 'Lift1'
LIFT2_ID = 'Lift2'
LIFTS = [LIFT1_ID, LIFT2_ID]

# --- Visualization Constants ---
CANVAS_HEIGHT = 600  # Verhoogd naar 600 voor meer ruimte
CANVAS_WIDTH = 200   # Breder voor U-vormige weergave
MAX_ROWS = 50  # Maximum aantal rijen per zijde (links + rechts = 100 totaal)
MIN_ROW = -2   # Minimum rij (service locatie)
LIFT_WIDTH = 80  # Liftbreedte
LIFT_HEIGHT_RATIO = 0.05 # Lift hoogte t.o.v. canvas hoogte
LIFT1_COLOR = 'blue'  # Kleur voor Lift1
LIFT2_COLOR = 'green'  # Kleur voor Lift2


class EcoSystemGUI_DualLift_ST:
    # ... (__init__ remains the same) ...
    def __init__(self, root):
        self.root = root
        self.root.title("Gibas EcoSystem Simulator (Dual Lift - ST Logic)")
        self.root.geometry("900x600") # Breder en compacter

        self.client = None
        self.plc_nodes = {LIFT1_ID: {}, LIFT2_ID: {}} # Only cache lift nodes now
        self.plc_ns_idx = 2
        self.is_connected = False
        self.monitoring_task = None
        self.last_watchdog_time = {LIFT1_ID: 0, LIFT2_ID: 0}
        self.watchdog_timeout = 3.0

        # --- GUI Setup ---
        conn_frame = ttk.LabelFrame(root, text="Connection", padding=10)
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

        # Hoofdframe met twee delen
        main_frame = ttk.Frame(root)
        main_frame.pack(expand=True, fill="both", padx=10, pady=5)

        # --- Gemeenschappelijke visualisatie schacht (links) ---
        vis_frame = ttk.LabelFrame(main_frame, text="Lift Visualization", padding=10)
        vis_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=5)

        # Canvas voor visualisatie van één schacht
        self.shared_canvas = tk.Canvas(vis_frame, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg='lightgrey')
        self.shared_canvas.pack(padx=5, pady=5)
        
        # Teken de liftschacht (één enkele schacht voor beide liften)
        self.shared_canvas.create_rectangle(
            10, 10, CANVAS_WIDTH - 10, CANVAS_HEIGHT - 10, 
            outline='black', width=2, fill='#f0f0f0'
        )
        
        # Teken service posities (-2 onderaan, 100 bovenaan)
        service_width = 60
        service_height = 30
        
        # Positie -2 (onderaan)
        self.shared_canvas.create_rectangle(
            CANVAS_WIDTH/2 - service_width/2, CANVAS_HEIGHT - 40,
            CANVAS_WIDTH/2 + service_width/2, CANVAS_HEIGHT - 10,
            outline='black', width=2, fill='#e0e0e0'
        )
        self.shared_canvas.create_text(
            CANVAS_WIDTH/2, CANVAS_HEIGHT - 25, text="-2", font=("Arial", 8)
        )
        
        # Positie 100 (bovenaan)
        self.shared_canvas.create_rectangle(
            CANVAS_WIDTH/2 - service_width/2, 10,
            CANVAS_WIDTH/2 + service_width/2, 40,
            outline='black', width=2, fill='#e0e0e0'
        )
        self.shared_canvas.create_text(
            CANVAS_WIDTH/2, 25, text="100", font=("Arial", 8)
        )
        
        # Schaalaanduiding toevoegen
        # Links tonen we rijindicaties 1 t/m 50
        for i in range(1, 51, 5):
            y_pos = 10 + (i / MAX_ROWS) * (CANVAS_HEIGHT - 20)
            self.shared_canvas.create_text(
                20, y_pos, text=str(i), anchor=tk.W, font=("Arial", 8)
            )
            
        # Rechts tonen we rijindicaties 51 t/m 99
        for i in range(51, 100, 5):
            y_pos = 10 + (i / MAX_ROWS) * (CANVAS_HEIGHT - 20)
            self.shared_canvas.create_text(
                CANVAS_WIDTH - 20, y_pos, text=str(i), anchor=tk.E, font=("Arial", 8)
            )
        
        # Lift platform parameters voor beide liften
        lift_y_size = CANVAS_HEIGHT * LIFT_HEIGHT_RATIO
        lift_width = LIFT_WIDTH
        
        # Initiële posities (worden later bijgewerkt obv echte waardes)
        lift_x_center = CANVAS_WIDTH / 2  # Midden van de schacht voor beide liften
        lift1_y = CANVAS_HEIGHT / 4   # 1/4 van boven
        lift2_y = CANVAS_HEIGHT * 3/4  # 3/4 van boven
        
        # Teken de liften
        lift1_rect = self.shared_canvas.create_rectangle(
            lift_x_center - lift_width/2, lift1_y,
            lift_x_center + lift_width/2, lift1_y + lift_y_size,
            fill=LIFT1_COLOR, tags=("Lift1_lift",)
        )
        
        lift2_rect = self.shared_canvas.create_rectangle(
            lift_x_center - lift_width/2, lift2_y,
            lift_x_center + lift_width/2, lift2_y + lift_y_size,
            fill=LIFT2_COLOR, tags=("Lift2_lift",)
        )
        
        # Teken vorken
        fork_width = 25
        fork_height = lift_y_size * 0.8
        
        # Lift1 vorken
        fork1_rect = self.shared_canvas.create_rectangle(
            lift_x_center - fork_width/2, lift1_y + lift_y_size*0.1,
            lift_x_center + fork_width/2, lift1_y + lift_y_size*0.9,
            fill='gray', tags=("Lift1_fork",)
        )
        
        # Lift2 vorken
        fork2_rect = self.shared_canvas.create_rectangle(
            lift_x_center - fork_width/2, lift2_y + lift_y_size*0.1,
            lift_x_center + fork_width/2, lift2_y + lift_y_size*0.9,
            fill='gray', tags=("Lift2_fork",)
        )
        
        # Tray objecten (initieel onzichtbaar)
        tray_width = 30
        tray_height = lift_y_size * 0.7
        
        # Lift1 tray
        tray1_rect = self.shared_canvas.create_rectangle(
            lift_x_center - tray_width/2, lift1_y + lift_y_size*0.15,
            lift_x_center + tray_width/2, lift1_y + lift_y_size*0.85,
            fill='orange', outline='brown', width=2,
            tags=("Lift1_tray",), state=tk.HIDDEN
        )
        
        # Lift2 tray
        tray2_rect = self.shared_canvas.create_rectangle(
            lift_x_center - tray_width/2, lift2_y + lift_y_size*0.15,
            lift_x_center + tray_width/2, lift2_y + lift_y_size*0.85,
            fill='orange', outline='brown', width=2,
            tags=("Lift2_tray",), state=tk.HIDDEN
        )
        
        # Opslaan van visualisatie-elementen
        self.lift_visuals = {
            LIFT1_ID: {
                'canvas': self.shared_canvas,
                'rect': lift1_rect,
                'fork': fork1_rect,
                'tray': tray1_rect,
                'lift_width': lift_width,
                'fork_width': fork_width,
                'tray_width': tray_width,
                'y_size': lift_y_size,
                'lift_x_center': lift_x_center  # Midden van de schacht
            },
            LIFT2_ID: {
                'canvas': self.shared_canvas,
                'rect': lift2_rect,
                'fork': fork2_rect,
                'tray': tray2_rect, 
                'lift_width': lift_width,
                'fork_width': fork_width,
                'tray_width': tray_width,
                'y_size': lift_y_size,
                'lift_x_center': lift_x_center  # Midden van de schacht
            }
        }

        # Notebook for Controls (rechts)
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(side=tk.RIGHT, expand=True, fill="both", padx=5, pady=5)

        self.lift_frames = {}
        self.status_labels = {}
        self.job_controls = {}
        self.ack_controls = {}
        self.error_controls = {}

        # Define the status variables to display, matching plcsim.py ST version
        status_vars_to_display = [
             "iCycle", "iStatus", "iElevatorRowLocation", 
             "xTrayInElevator", "iCurrentForkSide",
             "ActiveElevatorAssignment_iTaskType", "ActiveElevatorAssignment_iOrigination",
             "ActiveElevatorAssignment_iDestination"
        ]
        
        # Maak speciale tekstlabel voor de uitgebreide staptekst
        special_vars = ["sSeq_Step_comment"]

        for lift_id in LIFTS:
            frame = ttk.Frame(self.notebook, padding="10")
            self.notebook.add(frame, text=lift_id)
            self.lift_frames[lift_id] = frame

            # --- Right Side Frame for Controls/Status ---
            right_frame = ttk.Frame(frame, padding="5")
            right_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
            # --- Move existing sections into right_frame ---

            # Status Section
            status_frame = ttk.LabelFrame(right_frame, text=f"{lift_id} Status", padding=10)
            status_frame.pack(fill=tk.X, pady=5)
            self.status_labels[lift_id] = {}
            row, col = 0, 0
            for var in status_vars_to_display:
                ttk.Label(status_frame, text=f"{var}:").grid(row=row, column=col, sticky=tk.W, padx=2, pady=1)
                val_label = ttk.Label(status_frame, text="N/A", width=35, anchor=tk.W, borderwidth=1, relief="groove") # Added border
                val_label.grid(row=row, column=col + 1, sticky=tk.W+tk.E, padx=2, pady=1) # Stretch label
                self.status_labels[lift_id][var] = val_label
                col += 2
                if col >= 4: col = 0; row += 1
                
            # Speciale sectie voor de staptekst met extra grote label
            step_frame = ttk.LabelFrame(right_frame, text=f"{lift_id} Stap Informatie", padding=10)
            step_frame.pack(fill=tk.X, pady=5)
            ttk.Label(step_frame, text="sSeq_Step_comment:").pack(side=tk.TOP, anchor=tk.W, padx=2, pady=1)
            
            # Gebruik een tekstbox in plaats van een label voor betere weergave
            seq_step_text = tk.Text(step_frame, 
                                    height=3,  # Verhoogd naar 3 regels voor meer ruimte
                                    width=90,  # Vergroot van 80 naar 90 
                                    borderwidth=1,
                                    relief="groove")
            seq_step_text.pack(fill=tk.X, padx=2, pady=1)
            seq_step_text.insert("1.0", "N/A")
            seq_step_text.config(state=tk.DISABLED)  # Alleen-lezen modus
            
            # Sla referentie op naar tekstbox
            self.status_labels[lift_id]["sSeq_Step_comment"] = seq_step_text

            # Job Control Section (remains similar)
            job_frame = ttk.LabelFrame(right_frame, text=f"{lift_id} Job Control", padding=10)
            job_frame.pack(fill=tk.X, pady=5)
            # ... (Radiobuttons, Origin, Destination Entries as before) ...
            controls = {}
            controls['task_type_var'] = tk.IntVar(value=1)
            ttk.Radiobutton(job_frame, text="1: Full Placement", variable=controls['task_type_var'], value=1).grid(row=0, column=1, sticky=tk.W)
            ttk.Radiobutton(job_frame, text="2: Move To", variable=controls['task_type_var'], value=2).grid(row=0, column=2, sticky=tk.W)
            ttk.Radiobutton(job_frame, text="3: Prepare PickUp", variable=controls['task_type_var'], value=3).grid(row=0, column=3, sticky=tk.W)
            ttk.Label(job_frame, text="Task Type:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)

            controls['origin_var'] = tk.IntVar(value=10 if lift_id == LIFT1_ID else 60) # Adjust defaults
            ttk.Label(job_frame, text="Origin:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
            ttk.Entry(job_frame, textvariable=controls['origin_var'], width=10).grid(row=1, column=1, sticky=tk.W, padx=5)

            controls['destination_var'] = tk.IntVar(value=50 if lift_id == LIFT1_ID else 20) # Adjust defaults
            ttk.Label(job_frame, text="Destination:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
            ttk.Entry(job_frame, textvariable=controls['destination_var'], width=10).grid(row=2, column=1, sticky=tk.W, padx=5)

            controls['send_job_button'] = ttk.Button(job_frame, text="Send Job Request",
                                                    command=lambda l=lift_id: self.send_job(l), state=tk.DISABLED)
            controls['send_job_button'].grid(row=3, column=0, columnspan=2, pady=10)
            
            # Voeg een nieuwe Clear Task knop toe
            controls['clear_task_button'] = ttk.Button(job_frame, text="Clear Task (Reset PLC)",
                                                      command=lambda l=lift_id: self.clear_task(l), state=tk.DISABLED)
            controls['clear_task_button'].grid(row=3, column=2, columnspan=2, pady=10)
            
            self.job_controls[lift_id] = controls

            # Handshake/Ack Section
            ack_frame = ttk.LabelFrame(right_frame, text=f"{lift_id} Handshake / Acknowledge", padding=10)
            ack_frame.pack(fill=tk.X, pady=5)
            ack_ctrls = {}
            ack_ctrls['ack_movement_button'] = ttk.Button(ack_frame, text="Acknowledge Job Step", # Renamed button
                                                        command=lambda l=lift_id: self.acknowledge_job_step(l), state=tk.DISABLED)
            ack_ctrls['ack_movement_button'].pack(side=tk.LEFT, padx=10)
            # Label to show *what* PLC expects ack for
            ack_ctrls['ack_info_label'] = ttk.Label(ack_frame, text="PLC Awaiting Ack: No", foreground="grey")
            ack_ctrls['ack_info_label'].pack(side=tk.LEFT, padx=10)
            self.ack_controls[lift_id] = ack_ctrls

            # Error Control Section (remains similar)
            error_frame = ttk.LabelFrame(right_frame, text=f"{lift_id} Error Control", padding=10)
            error_frame.pack(fill=tk.X, pady=5)
            # ... (Clear Error button and label as before) ...
            err_ctrls = {}
            err_ctrls['clear_error_button'] = ttk.Button(error_frame, text="Clear Error",
                                                         command=lambda l=lift_id: self.clear_error(l), state=tk.DISABLED)
            err_ctrls['clear_error_button'].pack(side=tk.LEFT, padx=10)
            err_ctrls['error_status_label'] = ttk.Label(error_frame, text="PLC Error State: No", foreground="green")
            err_ctrls['error_status_label'].pack(side=tk.LEFT, padx=10)
            self.error_controls[lift_id] = err_ctrls

    # --- OPC UA Methods ---
    async def _get_node(self, node_path_str):
        """Helper to get a node object from a path string like 'Lift1/iCycle'.
        Uses manual browsing with get_child and detailed logging.
        Assumes structure: Objects / LiftSystem / LiftX / Variable
        """
        if not self.client or not self.is_connected:
            logger.warning("_get_node called without client connection.")
            return None

        try:
            parts = node_path_str.split('/')
            # Start browsing from the Objects folder
            current_node = self.client.get_objects_node()
            logger.debug(f"_get_node: Starting browse for '{node_path_str}' from Objects node: {current_node}")

            # 1. Browse for 'LiftSystem' object
            parent_folder_name = "LiftSystem"
            qualified_parent_name = f"{self.plc_ns_idx}:{parent_folder_name}"
            logger.debug(f"_get_node: Attempting to get child: {qualified_parent_name}")
            try:
                lift_system_node = await current_node.get_child(qualified_parent_name)
                if not lift_system_node:
                    logger.error(f"_get_node: Parent folder '{parent_folder_name}' (QN: {qualified_parent_name}) not found under Objects node.")
                    # Log children of Objects node for debugging
                    children = await current_node.get_children()
                    logger.debug(f"_get_node: Children of Objects node: {children}")
                    bnames = [await child.read_browse_name() for child in children]
                    logger.debug(f"_get_node: BrowseNames of Objects children: {bnames}")
                    return None
                logger.debug(f"_get_node: Found LiftSystem node: {lift_system_node}")
                current_node = lift_system_node
            except ua.UaStatusCodeError as e:
                 logger.error(f"_get_node: OPC UA Error browsing for '{parent_folder_name}': {e}")
                 return None
            except Exception as e:
                 logger.exception(f"_get_node: Unexpected error browsing for '{parent_folder_name}': {e}")
                 return None

            # 2. Browse for subsequent parts (e.g., 'Lift1', 'iCycle') under LiftSystem
            for i, part in enumerate(parts):
                qualified_part_name = f"{self.plc_ns_idx}:{part}"
                logger.debug(f"_get_node: Attempting to get child '{part}' (QN: {qualified_part_name}) from node {current_node}")
                try:
                    child_node = await current_node.get_child(qualified_part_name)
                    if not child_node:
                        logger.error(f"_get_node: Child '{part}' (QN: {qualified_part_name}) not found under node {current_node}.")
                        # Log children for debugging
                        children = await current_node.get_children()
                        logger.debug(f"_get_node: Children of {current_node}: {children}")
                        bnames = [await child.read_browse_name() for child in children]
                        logger.debug(f"_get_node: BrowseNames of children: {bnames}")
                        return None
                    logger.debug(f"_get_node: Found child node for '{part}': {child_node}")
                    current_node = child_node
                except ua.UaStatusCodeError as e:
                    logger.error(f"_get_node: OPC UA Error browsing for child '{part}': {e}")
                    return None
                except Exception as e:
                    logger.exception(f"_get_node: Unexpected error browsing for child '{part}': {e}")
                    return None

            # If loop completes, current_node is the target node
            logger.debug(f"_get_node: Successfully found node for path '{node_path_str}': {current_node}")
            return current_node

        except Exception as e:
            logger.exception(f"_get_node: Unexpected Error in _get_node for path '{node_path_str}': {e}")
            return None

    async def _read_value(self, identifier):
        """Read value using 'Lift1/iCycle' identifier."""
        if not self.is_connected or not self.client: return None
        node = await self._get_node(identifier)
        if not node:
            # logger.warning(f"Read failed: Node not found for {identifier}") # DEBUG: Log node not found here
            return None # Node not found by _get_node
        try:
            value = await node.read_value()
            return value
        except ua.UaStatusCodeError as e: # Use ua.UaStatusCodeError
             logger.error(f"OPC UA Error reading value for {identifier}: {e} (Code: {e.code})")
             return None
        except Exception as e:
             logger.exception(f"Unexpected Error reading value for {identifier}: {e}")
             return None
        
    async def _write_value(self, identifier, value, datatype=None):
        """Write value using 'Lift1/Eco_iTaskType' identifier."""
        if not self.is_connected or not self.client: return False

        node = await self._get_node(identifier)
        if not node:
             logger.error(f"Node not found for writing: {identifier}")
             messagebox.showerror("OPC UA Error", f"Node not found for writing: {identifier}")
             return False
        try:
            # Originele implementatie - eenvoudig direct schrijven zonder typecasting
            # Dit werkte daadwerkelijk in eerdere versies
            logger.info(f"Writing value: {value} to {identifier}")
            await node.write_value(value)
            return True
            
        except ua.UaStatusCodeError as e:
             logger.error(f"OPC UA Error writing node {identifier} with value {value}: {e} (Code: {e.code})")
             messagebox.showerror("OPC UA Write Error", f"Failed to write {identifier}: {e}")
             return False
        except Exception as e:
            logger.exception(f"Unexpected Error writing node {identifier} with value {value}: {e}")
            messagebox.showerror("OPC UA Write Error", f"Failed to write {identifier}: {e}")
            return False

    # --- GUI Update Logic (_update_gui_status remains the same with robustness checks) ---
    # (Use the version with safe_get_int from the previous response)
    def _update_gui_status(self, lift_id, plc_data):
        """Update GUI labels AND lift visualization for a specific lift."""
        if lift_id not in self.status_labels: return

        # Helper to safely get int or return 0
        def safe_get_int(key, default=0):
            val = plc_data.get(key, default)
            # Check if it's None or string before returning default
            if val is None or isinstance(val, str):
                return default
            # Handle potential conversion errors if value is not directly int
            try:
                return int(val)
            except (ValueError, TypeError):
                return default


        # Update Status Labels
        for name, label in self.status_labels[lift_id].items():
            value = plc_data.get(name, "N/A") # Use N/A if data missing
            
            # Format specific fields for clarity
            if name == "sSeq_Step_comment" and value == "": 
                value = "(No comment)"
            elif name == "sSeq_Step_comment":
                # Speciale verwerking voor stappentekst
                if isinstance(value, bytes):
                    try:
                        value = value.decode('utf-8', errors='ignore')
                    except:
                        value = str(value)
                value = str(value)
                # Log de waarde om te zien of er problemen zijn
                logger.info(f"Stap tekst update: {value}")
                
            elif name.startswith("s") and isinstance(value, bytes): # Decode strings
                try:
                    value = value.decode('utf-8', errors='ignore')
                except:
                    pass
                    
            if isinstance(value, float): 
                value = f"{value:.2f}" # Format floats
                
            # Update label text, ensuring it's a string
            if isinstance(label, tk.Text):
                label.config(state=tk.NORMAL)
                label.delete("1.0", tk.END)
                label.insert("1.0", str(value))
                label.config(state=tk.DISABLED)
                # Forceer een update van het GUI-element
                label.update_idletasks()
            else:
                label.config(text=str(value))


        # --- Button State Logic with Logging ---
        ack_type = safe_get_int("EcoAck_iAssingmentType")
        ack_row = safe_get_int("EcoAck_iRowNr")
        ack_needed = ack_type > 0
        error_code = safe_get_int("iErrorCode")
        is_error = error_code != 0
        plc_cycle = safe_get_int("iCycle", -1)

        # Acknowledge Button Logic
        ack_info_text = "PLC Awaiting Ack: No"
        ack_button_state = tk.DISABLED
        ack_label_color = "grey"
        if ack_needed and self.is_connected:
            ack_type_str = "GetTray" if ack_type == 1 else "SetTray" if ack_type == 2 else f"Type {ack_type}"
            ack_info_text = f"PLC Awaiting Ack: {ack_type_str} @ Row {ack_row}"
            ack_button_state = tk.NORMAL
            ack_label_color = "orange"
        logger.debug(f"[{lift_id}] Ack Button Logic: connected={self.is_connected}, ack_needed={ack_needed} (type={ack_type}) -> state={ack_button_state}")
        if lift_id in self.ack_controls:
             self.ack_controls[lift_id]['ack_info_label'].config(text=ack_info_text, foreground=ack_label_color)
             self.ack_controls[lift_id]['ack_movement_button'].config(state=ack_button_state)

        # Error Button Logic
        error_info_text = f"PLC Error State: No (Code: {error_code})"
        error_button_state = tk.DISABLED
        error_label_color = "green"
        if is_error and self.is_connected:
             error_info_text = f"PLC Error State: YES (Code: {error_code})"
             error_button_state = tk.NORMAL
             error_label_color = "red"
        logger.debug(f"[{lift_id}] Error Button Logic: connected={self.is_connected}, is_error={is_error} (code={error_code}) -> state={error_button_state}")
        if lift_id in self.error_controls:
             self.error_controls[lift_id]['error_status_label'].config(text=error_info_text, foreground=error_label_color)
             self.error_controls[lift_id]['clear_error_button'].config(state=error_button_state)

        # Job Send Button Logic
        can_send_job = self.is_connected and not is_error and plc_cycle == 10 
        job_button_state = tk.NORMAL if can_send_job else tk.DISABLED
        logger.debug(f"[{lift_id}] Job Button Logic: connected={self.is_connected}, is_error={is_error}, plc_cycle={plc_cycle} -> state={job_button_state}")
        if lift_id in self.job_controls:
             self.job_controls[lift_id]['send_job_button'].config(state=job_button_state)

        # Clear Task Button Logic
        # Activeer de Clear Task knop wanneer:
        # 1. Er een taak actief is (cycle niet 0 of 10)
        # 2. Een taak wacht op een reset (Task Done/Assignment Rejected)
        waiting_clear = (
            "Done - Waiting" in str(plc_data.get("sSeq_Step_comment", "")) or
            "Rejected" in str(plc_data.get("sSeq_Step_comment", ""))
        )
        needs_clear = self.is_connected and (
            (plc_cycle not in [0, 10] and plc_cycle > 0) or  # Actieve taak
            waiting_clear  # Wachtend op clear
        )
        clear_button_state = tk.NORMAL if needs_clear else tk.DISABLED
        logger.debug(f"[{lift_id}] Clear Button Logic: connected={self.is_connected}, plc_cycle={plc_cycle}, waiting_clear={waiting_clear} -> state={clear_button_state}")
        if lift_id in self.job_controls:
            self.job_controls[lift_id]['clear_task_button'].config(state=clear_button_state)


        # --- Enhanced Lift Visualization with Forks and Tray ---
        if lift_id in self.lift_visuals:
            # Get relevant state data
            current_row = safe_get_int("iElevatorRowLocation")
            has_tray = plc_data.get("xTrayInElevator", False)
            fork_side = safe_get_int("iCurrentForkSide", 0)  # 0=middle, 1=left, 2=right
            
            vis = self.lift_visuals[lift_id]
            canvas = vis['canvas']
            lift_rect = vis['rect']
            fork_rect = vis['fork']
            tray_rect = vis['tray']
            lift_y_size = vis['y_size']
            lift_x_center = vis['lift_x_center']
            lift_width = vis['lift_width']
            fork_width = vis['fork_width']
            tray_width = vis['tray_width']

            # 1. Calculate Y position for the lift (vertical movement)
            # Row 0 = top (y=5), Row MAX_ROWS = bottom
            clamped_row = max(0, min(MAX_ROWS, current_row))
            row_proportion = clamped_row / MAX_ROWS if MAX_ROWS > 0 else 0
            movable_height = CANVAS_HEIGHT - 10 - lift_y_size
            new_y1 = 5 + row_proportion * movable_height
            new_y2 = new_y1 + lift_y_size
            
            # 2. Update lift rectangle position (vertical only)
            coords = canvas.coords(lift_rect)
            x1, _, x2, _ = coords
            canvas.coords(lift_rect, x1, new_y1, x2, new_y2)
            
            # 3. Calculate fork position based on fork_side
            # Left (1), Middle (0), Right (2)
            fork_offset = 0  # Horizontal offset from center
            if fork_side == 1:  # Left/RobotSide
                fork_offset = -lift_width/2 + fork_width/2
            elif fork_side == 2:  # Right/OperatorSide
                fork_offset = lift_width/2 - fork_width/2
                
            # 4. Update fork rectangle position (both vertical and horizontal)
            fork_x1 = lift_x_center - fork_width/2 + fork_offset
            fork_x2 = lift_x_center + fork_width/2 + fork_offset
            fork_y1 = new_y1 + lift_y_size*0.1  # Slightly inset from lift
            fork_y2 = new_y1 + lift_y_size*0.9
            canvas.coords(fork_rect, fork_x1, fork_y1, fork_x2, fork_y2)
            
            # 5. Update tray visibility and position
            if has_tray:
                # Show tray on the forks with proper position
                tray_x1 = fork_x1 + fork_width/2 - tray_width/2
                tray_x2 = fork_x1 + fork_width/2 + tray_width/2
                tray_y1 = new_y1 + lift_y_size*0.15  # Slightly inset from lift
                tray_y2 = new_y1 + lift_y_size*0.85
                canvas.coords(tray_rect, tray_x1, tray_y1, tray_x2, tray_y2)
                canvas.itemconfig(tray_rect, state=tk.NORMAL)  # Make tray visible
            else:
                canvas.itemconfig(tray_rect, state=tk.HIDDEN)  # Hide tray
                
            # 6. Update lift color based on state 
            # (Now mostly for additional visual states if needed)
            lift_color = 'blue'  # Default color
            if is_error:
                lift_color = 'red'  # Error state
            canvas.itemconfig(lift_rect, fill=lift_color)


    # --- Monitoring Loop ---
    async def _monitor_plc(self):
        """Periodically read status for both lifts based on new ST vars."""
        logger.info("Starting Dual Lift PLC monitoring task (ST Logic).")
        
        # Voeg handshake en error variabelen toe aan de monitoring
        variables_to_read_per_lift = list(self.status_labels[LIFT1_ID].keys())
        handshake_vars = ["EcoAck_iAssingmentType", "EcoAck_iRowNr", "EcoAck_xAcknowldeFromEco", 
                         "iErrorCode", "xWatchDog"]
        
        # Voeg handshake variabelen toe aan monitoring lijst
        for var in handshake_vars:
            if var not in variables_to_read_per_lift:
                variables_to_read_per_lift.append(var)
                
        logger.info(f"Monitoring variables: {variables_to_read_per_lift}")

        # --- DEBUG: Try reading one known variable first ---
        logger.info("Attempting initial single read...")
        test_id = f"{LIFT1_ID}/iCycle"
        test_val = await self._read_value(test_id)
        if test_val is None:
             logger.error(f"INITIAL READ FAILED for {test_id}. Aborting monitor.")
             self._handle_connection_error()
             return # Stop the monitor if initial read fails
        else:
             logger.info(f"Initial read successful: {test_id} = {test_val}")
        # --- END DEBUG ---


        while self.is_connected:
            try:
                connection_ok_overall = True # Track if any read fails
                for lift_id in LIFTS:
                    plc_data = {}
                    lift_connection_ok = True
                    failed_vars = [] # Track specific failures for this lift

                    for var_name in variables_to_read_per_lift:
                        identifier = f"{lift_id}/{var_name}"
                        value = await self._read_value(identifier)
                        if value is not None:
                            plc_data[var_name] = value
                        else:
                            # Log specific variable read failure only once?
                            failed_vars.append(var_name)
                            lift_connection_ok = False
                            connection_ok_overall = False
                            # break # Stop reading for this lift if one fails? Maybe continue to see all failures

                    if failed_vars:
                        logger.warning(f"Read failure for {lift_id}. Failed vars: {failed_vars}")

                    # Update watchdog time if read was successful OR WD specifically read ok
                    wd_read_ok = "xWatchDog" in plc_data and plc_data["xWatchDog"] is not None
                    if lift_connection_ok or wd_read_ok:
                         self.last_watchdog_time[lift_id] = time.time()
                    elif not lift_connection_ok: # Log explicitly if WD couldn't be checked due to general failure
                         logger.warning(f"Watchdog time not updated for {lift_id} due to read failures.")


                    # Update GUI for this lift if still connected
                    if self.is_connected:
                         self._update_gui_status(lift_id, plc_data)


                # Check Watchdog Timeouts after checking both lifts
                now = time.time()
                for lift_id in LIFTS:
                     # Only check timeout if connection was previously ok for this lift
                     # This prevents immediate timeout trigger if initial reads failed
                     if lift_connection_ok: # Check status from *this* loop iteration
                          if now - self.last_watchdog_time[lift_id] > self.watchdog_timeout:
                              logger.warning(f"Watchdog timeout for {lift_id}!")
                              connection_ok_overall = False
                              break # One timeout triggers disconnect logic


                if not connection_ok_overall and self.is_connected:
                     logger.error("Detected read failure or watchdog timeout during monitoring.")
                     self._handle_connection_error() # Trigger disconnect logic

                # Await slightly longer to reduce load?
                await asyncio.sleep(0.5) # Monitoring interval

            except asyncio.CancelledError:
                logger.info("Monitoring task cancelled.")
                break
            except Exception as e:
                logger.exception(f"Unhandled Error in monitoring loop: {e}") # Log full traceback
                self._handle_connection_error()
                break

        logger.info("PLC monitoring task stopped.")
        if self.is_connected: # Ensure GUI updates if loop terminates unexpectedly
             self._update_connection_status(False)


    # --- Connection/Disconnection Methods (_handle_connection_error, _update_connection_status, connect_plc, disconnect_plc, _async_disconnect) ---
    # Add the delay in _async_connect
    async def _async_connect(self):
        try:
            await self.client.connect()
            logger.info("Client Connected.")
            self.plc_ns_idx = await self.client.get_namespace_index(PLC_NS_URI)
            logger.info(f"PLC Namespace Index: {self.plc_ns_idx}")

            # --- ADD DELAY ---
            logger.info("Waiting 1 second before starting monitor...")
            await asyncio.sleep(1.0)
            # --- END DELAY ---

            self._update_connection_status(True)
            self.plc_nodes.clear()

            now = time.time()
            for lift_id in LIFTS: self.last_watchdog_time[lift_id] = now

            if self.monitoring_task: self.monitoring_task.cancel()
            self.monitoring_task = asyncio.create_task(self._monitor_plc())

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            messagebox.showerror("Connection Failed", f"Could not connect to {self.endpoint_var.get()}\nError: {e}")
            try:
                 if self.client: await self.client.disconnect()
            except: pass
            self._update_connection_status(False)

    def _handle_connection_error(self):
         if self.is_connected:
             logger.error("Connection error detected.")
             messagebox.showerror("Connection Error", "Lost connection to PLC or failed to communicate.")
             self.disconnect_plc() # Cleanly disconnect and update GUI


    def _update_connection_status(self, connected):
        # (Use the version with robustness checks from previous response)
        self.is_connected = connected
        status_text = "Status: Connected" if connected else "Status: Disconnected"
        status_color = "green" if connected else "red"
        btn_conn_state = tk.DISABLED if connected else tk.NORMAL
        btn_disconn_state = tk.NORMAL if connected else tk.DISABLED

        self.conn_status_label.config(text=status_text, foreground=status_color)
        self.connect_button.config(state=btn_conn_state)
        self.disconnect_button.config(state=btn_disconn_state)

        # Helper to safely get int or return 0
        def safe_get_int(key, default=0, data=None): # Added data param
            if data is None: data = {}
            val = data.get(key, default)
            if val is None or isinstance(val, str): return default
            return val if isinstance(val, int) else default

        for lift_id in LIFTS:
            # Create empty data structure with N/A for reset
            empty_data = {name: "N/A" for name in self.status_labels[lift_id]}
            if not connected:
                 self._update_gui_status(lift_id, empty_data) # Reset GUI if disconnected

            # Disable buttons regardless of GUI update success if disconnected
            if lift_id in self.job_controls:
                 self.job_controls[lift_id]['send_job_button'].config(state=tk.DISABLED)
            if lift_id in self.ack_controls:
                 self.ack_controls[lift_id]['ack_movement_button'].config(state=tk.DISABLED)
                 self.ack_controls[lift_id]['ack_info_label'].config(text="PLC Awaiting Ack: N/A", foreground="grey")
            if lift_id in self.error_controls:
                 self.error_controls[lift_id]['clear_error_button'].config(state=tk.DISABLED)
                 self.error_controls[lift_id]['error_status_label'].config(text="PLC Error State: N/A", foreground="grey")

    # ... (Rest of the connection/disconnection methods remain the same) ...
    def connect_plc(self):
        endpoint = self.endpoint_var.get()
        logger.info(f"Attempting to connect to {endpoint}")
        self.client = Client(url=endpoint)
        asyncio.create_task(self._async_connect())

    def disconnect_plc(self):
        logger.info("Disconnecting from PLC...")
        if self.monitoring_task:
            self.monitoring_task.cancel()
            self.monitoring_task = None

        if self.client and self.is_connected:
            asyncio.create_task(self._async_disconnect())
        else:
             self._update_connection_status(False) # Already disconnected or no client

    async def _async_disconnect(self):
         """Perform the actual client disconnect."""
         was_connected = self.is_connected
         self.is_connected = False # Prevent monitor loop interference during disconnect

         if self.client:
             try:
                 await self.client.disconnect()
                 logger.info("Client disconnected successfully.")
             except Exception as e:
                 logger.error(f"Error during disconnect: {e}")
             finally:
                 self.client = None
         self._update_connection_status(False)


    # --- Action Methods (send_job, acknowledge_job_step, clear_error) ---
    # (These remain functionally the same)
    def send_job(self, lift_id):
        """Send job parameters using the new variable names."""
        if not self.is_connected:
             messagebox.showwarning("Not Connected", "Cannot send job, not connected to PLC.")
             return
        controls = self.job_controls[lift_id]
        task_type = controls['task_type_var'].get()
        origin = controls['origin_var'].get()
        destination = controls['destination_var'].get()
        logger.info(f"Sending Job to {lift_id}: Type={task_type}, Origin={origin}, Dest={destination}")

        async def job_write_sequence():
            try:
                # Schrijf de waarden als Python primitives (int)
                # Laat OPC UA zelf de juiste conversie doen
                logger.info(f"Writing origin={origin} to {lift_id}/Eco_iOrigination")
                origin_success = await self._write_value(f"{lift_id}/Eco_iOrigination", origin)
                
                if not origin_success:
                    logger.error(f"Failed to write origin to {lift_id}")
                    return
                
                logger.info(f"Writing destination={destination} to {lift_id}/Eco_iDestination")
                dest_success = await self._write_value(f"{lift_id}/Eco_iDestination", destination)
                
                if not dest_success:
                    logger.error(f"Failed to write destination to {lift_id}")
                    return
                
                # Wacht kort om ervoor te zorgen dat origin en destination zijn verwerkt
                await asyncio.sleep(0.1)
                
                # Schrijf task_type als laatste om de job te triggeren
                logger.info(f"Writing task_type={task_type} to {lift_id}/Eco_iTaskType")
                task_success = await self._write_value(f"{lift_id}/Eco_iTaskType", task_type)
                
                if task_success:
                    logger.info(f"Job parameters sent successfully to {lift_id}")
                else:
                    logger.error(f"Failed to write task_type to {lift_id}")
                
            except Exception as e:
                logger.exception(f"Error sending job: {e}")
                messagebox.showerror("Job Error", f"Exception sending job: {e}")

        asyncio.create_task(job_write_sequence())

    def acknowledge_job_step(self, lift_id):
        """Acknowledge the PLC's handshake request."""
        if not self.is_connected:
             messagebox.showwarning("Not Connected", "Cannot acknowledge, not connected to PLC.")
             return
        logger.info(f"Sending Job Step Acknowledge for {lift_id} (EcoAck_xAcknowldeFromEco = True)")
        
        # Schrijf de acknowledgement met asyncio functie voor betere betrouwbaarheid
        async def send_acknowledge():
            # Schrijf TRUE waarde
            logger.info(f"Writing xAcknowldeFromEco = True to {lift_id}")
            await self._write_value(f"{lift_id}/EcoAck_xAcknowldeFromEco", True)
            
            # Wacht 0.5 seconde om PLC tijd te geven om te reageren
            # Dit is cruciaal, want te snel resetten zorgt dat de PLC de waarde niet ziet
            await asyncio.sleep(0.5)
            
            # Geen expliciet reset naar False, PLCSim zet waarde terug naar False
        
        # Start de async functie
        asyncio.create_task(send_acknowledge())

    def clear_error(self, lift_id):
        """Send clear error request."""
        if not self.is_connected:
            messagebox.showwarning("Not Connected", "Cannot clear error, not connected to PLC.")
            return
        logger.info(f"Sending Clear Error Request for {lift_id} (xClearError = True)")
        asyncio.create_task(self._write_value(f"{lift_id}/xClearError", True, ua.VariantType.Boolean))

    def clear_task(self, lift_id):
        """Stuur een TaskType=0 om de taak te wissen en de PLC te resetten."""
        if not self.is_connected:
            messagebox.showwarning("Niet Verbonden", "Kan taak niet wissen, geen verbinding met PLC.")
            return
        
        logger.info(f"Stuur Clear Task (TaskType=0) naar {lift_id}")
        
        async def clear_task_sequence():
            try:
                # Stuur TaskType 0 om de taak te wissen en de PLC te resetten
                logger.info(f"Writing task_type=0 (Clear) to {lift_id}/Eco_iTaskType")
                success = await self._write_value(f"{lift_id}/Eco_iTaskType", 0)
                
                if success:
                    logger.info(f"Clear Task succesvol verzonden naar {lift_id}")
                else:
                    logger.error(f"Clear Task verzenden mislukt voor {lift_id}")
                
            except Exception as e:
                logger.exception(f"Error bij het verzenden van Clear Task: {e}")
                messagebox.showerror("Clear Task Fout", f"Fout bij het verzenden van Clear Task: {e}")
        
        # Start de asynchrone functie
        asyncio.create_task(clear_task_sequence())


# --- Main Execution Setup (remains the same) ---
# ... (run_gui, main, __name__ == "__main__" block as before) ...
async def run_gui(root):
    while True:
        try:
            root.update()
            await asyncio.sleep(0.05)
        except tk.TclError as e:
             if "application has been destroyed" in str(e):
                 logger.info("GUI window closed.")
                 break
             else: raise

async def main():
    root = tk.Tk()
    gui = EcoSystemGUI_DualLift_ST(root)
    
    def on_closing():
        logger.info("Close button clicked.")
        if gui.is_connected:
            gui.disconnect_plc()
        root.destroy()  # Immediately destroy the window
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    gui_task = asyncio.create_task(run_gui(root))
    
    try: 
        await gui_task
    except asyncio.CancelledError: 
        logger.info("GUI task cancelled.")
    except Exception as e:
        logger.error(f"Error in GUI task: {e}")
    finally:
        # Ensure disconnect is attempted if window is closed forcefully
        if gui.is_connected:
             gui.disconnect_plc()
             await asyncio.sleep(0.3) # Give disconnect a chance
        if root.winfo_exists(): 
            root.destroy()

if __name__ == "__main__":
    try:
        # asyncio.run(main(), debug=True) # Enable asyncio debug if needed
        asyncio.run(main())
    except KeyboardInterrupt: logger.info("EcoSystem Simulator stopped by user.")
    finally: logger.info("EcoSystem Simulator Finished.")