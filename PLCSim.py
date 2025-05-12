import asyncio
import logging
from asyncua import Server, ua
import random
import time
import os
import sys

# Global variable for auto tasking mode
AUTO_TASKING_ENABLED = False

# Zorg dat de logs map bestaat
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Clear log file at startup
log_filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'plcsim.log')
# Clear the log file first if it exists
if (os.path.exists(log_filename)):
    try:
        open(log_filename, 'w').close()
        print(f"Cleared log file: {log_filename}")
    except Exception as e:
        print(f"Warning: Could not clear log file {log_filename}: {e}")

log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler(log_filename, mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("PLCSim_DualLift")

# Set asyncua loggers to ERROR level to reduce verbosity
logging.getLogger("asyncua").setLevel(logging.ERROR)

LIFT1_ID = 'Lift1'
LIFT2_ID = 'Lift2'
LIFTS = [LIFT1_ID, LIFT2_ID]

# Task Type Constants
FullAssignment = 1
MoveToAssignment = 2
PreparePickUp = 3
BringAway = 4

# Fork Side Constants
MiddenLocation = 0
RobotSide = 1
OpperatorSide = 2

# Station Status Constants
STATUS_NOT_APPLICABLE = 0
STATUS_OK = 1
STATUS_NOTIFICATION = 2
STATUS_WARNING = 3
STATUS_ERROR = 4
STATUS_BOOTING = 5
STATUS_OFFLINE = 6
STATUS_SEMI_AUTO = 7
STATUS_TEACH = 8
STATUS_HAND = 9
STATUS_HOME = 10
STATUS_STOP = 11

# Cancel Assignment Reasons
CANCEL_PICKUP_WITH_TRAY = 1
CANCEL_DESTINATION_OUT_OF_REACH = 2
CANCEL_ORIGIN_OUT_OF_REACH = 3
CANCEL_INVALID_ZERO_POSITION = 4
CANCEL_LIFTS_CROSS = 5
CANCEL_INVALID_ASSIGNMENT = 6

class PLCSimulator_DualLift:
    def __init__(self, endpoint="opc.tcp://127.0.0.1:4860/gibas/plc/"):
        self.server = Server()
        self.endpoint = endpoint
        self.namespace_idx = None
        self.nodes = {LIFT1_ID: {}, LIFT2_ID: {}, 'System': {}}
        self.running = False
        self._task_duration = 2.0  # Simulation speed
        self._pickup_offset = 2
        
        # Functie om interne positie om te zetten naar fysieke positie
        # Voor de operatorzijde: 1-50 blijft 1-50
        # Voor de robotzijde: 51-99 wordt 1-49
        self.to_physical_pos = lambda pos: pos if pos <= 50 else pos - 50
        
        # Functie om te bepalen aan welke kant een positie zich bevindt
        self.get_side = lambda pos: "operator" if pos <= 50 else "robot"

        # State templates
        self.lift_state_template = {
            # --- Status ---
            "iCycle": 0,
            "iStatus": 0,
            "iStationStatus": STATUS_BOOTING,  # Start with booting status
            "iErrorCode": 0,
            "sShortAlarmDescription": "",
            "sAlarmMessage": "",
            "sAlarmSolution": "",
            "sSeq_Step_comment": "Initializing",
            # "xWatchDog": False, # Removed: xWatchDog is now a system-level variable

            # --- Job Assignment (Inputs from EcoSystem) ---
            # Removed old Eco_ variables, use ElevatorEcoSystAssignment directly from OPC UA
            # "Eco_iTaskType": 0,
            # "Eco_iOrigination": 0,
            # "Eco_iDestination": 0,

            # --- Active Job (Internal processing) ---
            "ActiveElevatorAssignment_iTaskType": 0,
            "ActiveElevatorAssignment_iOrigination": 0,
            "ActiveElevatorAssignment_iDestination": 0,

            # --- Handshake ---
            "iAssignmentType": 0, # This is for PLC-to-EcoSystem handshake (e.g. GetTray/SetTray)
            "iRowNr": 0,          # This is for PLC-to-EcoSystem handshake
            # "EcoAck_xAcknowldeFromEco": False, # Removed: Use ElevatorEcoSystAssignment.xAcknowledgeMovement

            # --- Lift State ---
            "iElevatorRowLocation": 0,
            "xTrayInElevator": False,
            "iCurrentForkSide": MiddenLocation,

            # --- Simulation Internals ---
            "_watchdog_plc_state": False,
            "_sub_fork_moving": False,
            "_sub_engine_moving": False,
            "_move_target_pos": 0,
            "_move_start_time": 0,
            "_fork_target_pos": MiddenLocation,
            "_fork_start_time": 0,
            "_current_job_valid": False
        }

        # System variables
        self.system_state = {
            "iAmountOfSations": 2,
            "iMainStatus": 1,
            "iCancelAssignment": 0,
            "xWatchDog": False  # EcoSystem status, written by EcoSystem, read by PLC
        }

        self.lift_state = {
            LIFT1_ID: self.lift_state_template.copy(),
            LIFT2_ID: self.lift_state_template.copy()
        }
        
        # Different starting positions
        self.lift_state[LIFT1_ID]['iElevatorRowLocation'] = 5  # Top of system
        self.lift_state[LIFT2_ID]['iElevatorRowLocation'] = 90  # Bottom of system
        self.lift_state[LIFT1_ID]['iCycle'] = 10  # Ready
        self.lift_state[LIFT2_ID]['iCycle'] = 10  # Ready

    async def _initialize_server(self):
        logger.info(f"Setting up dual-lift server on endpoint: {self.endpoint}")
        
        # Initialize server with minimal setup
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name("Gibas Dual Lift PLC Simulator")
        
        # Wait for initialization
        await asyncio.sleep(0.5)
        
        # Register namespace
        uri = "http://gibas.com/plc/"
        self.namespace_idx = await self.server.register_namespace(uri)
        logger.info(f"Registered namespace with index: {self.namespace_idx}")
        
        # Wait before creating objects
        await asyncio.sleep(0.5)
        
        # Create objects structure
        objects = self.server.nodes.objects
        lift_system = await objects.add_object(self.namespace_idx, "LiftSystem")
        logger.info(f"Created LiftSystem object node")

        # Create System-level Variables
        logger.info("Creating system-level variables")
        for name, value in self.system_state.items():
            ua_type = ua.VariantType.String  # Default
            if isinstance(value, bool): 
                ua_type = ua.VariantType.Boolean
            elif isinstance(value, int): 
                ua_type = ua.VariantType.Int16
            elif isinstance(value, float): 
                ua_type = ua.VariantType.Float

            try:
                node = await lift_system.add_variable(self.namespace_idx, name, value, datatype=ua_type)
                await node.set_writable()
                self.nodes['System'][name] = node
                logger.info(f"  Created system variable {name} ({ua_type.name}) with value {value}")
            except Exception as e:
                logger.error(f"Failed to create system variable '{name}': {e}")

        # Create Lift objects and variables
        for lift_id in LIFTS:
            # Set initial state to Ready
            self.lift_state[lift_id]['iCycle'] = 10
            self.lift_state[lift_id]['iStatus'] = 10
            self.lift_state[lift_id]['sSeq_Step_comment'] = "Ready - Waiting for Job Assignment"
            
            # Add Lift object under LiftSystem
            lift_obj = await lift_system.add_object(self.namespace_idx, lift_id)
            logger.info(f"Creating variables for {lift_id}")
            
            # Add StationData object
            station_data_obj = await lift_obj.add_object(self.namespace_idx, "StationData")
            logger.info(f"Created StationData object for {lift_id}")
            
            # Add Handshake object
            handshake_obj = await station_data_obj.add_object(self.namespace_idx, "Handshake")
            logger.info(f"Created Handshake object for {lift_id}")
            
            # Create hierarchical variables
            hierarchical_vars = {
                lift_obj: [
                    # ("xWatchDog", "xWatchDog", None), # Removed: xWatchDog is now a system-level variable
                    ("iMainStatus", "iStatus", None),
                    ("iCancelAssignmentReson", "iCancelAssignmentReson", None), # Note: This seems like a typo, perhaps "Reason"? Keeping as is for now.
                    ("iElevatorRowLocation", "iElevatorRowLocation", None),
                    ("xTrayInElevator", "xTrayInElevator", None),
                    ("iCurrentForkSide", "iCurrentForkSide", None),
                    ("iErrorCode", "iErrorCode", None),
                    ("xClearError", False, ua.VariantType.Boolean),
                    ("ActiveElevatorAssignment_iTaskType", "ActiveElevatorAssignment_iTaskType", None),
                    ("ActiveElevatorAssignment_iOrigination", "ActiveElevatorAssignment_iOrigination", None),
                    ("ActiveElevatorAssignment_iDestination", "ActiveElevatorAssignment_iDestination", None),
                    ("sSeq_Step_comment", "sSeq_Step_comment", None),
                ],
                station_data_obj: [
                    ("iCycle", "iCycle", None),
                    ("iStationStatus", "iStationStatus", None),
                    ("sStationStateDescription", "sSeq_Step_comment", None),
                    ("sShortAlarmDescription", "sShortAlarmDescription", None),
                    ("sAlarmSolution", "sAlarmSolution", None),
                ],
                handshake_obj: [
                    ("iRowNr", "iRowNr", None),
                    ("iJobType", "iAssignmentType", None),
                ]
            }
            
            # Create all variables in their hierarchical structure
            for parent_obj, var_list in hierarchical_vars.items():
                for var_name, source_key, ua_type_override in var_list:
                    value = self.lift_state[lift_id].get(source_key, "")
                    
                    # Determine UA type
                    if ua_type_override:
                        ua_type = ua_type_override
                    else:
                        ua_type = ua.VariantType.String  # Default
                        if isinstance(value, bool):
                            ua_type = ua.VariantType.Boolean
                        elif isinstance(value, int):
                            ua_type = ua.VariantType.Int16
                        elif isinstance(value, float):
                            ua_type = ua.VariantType.Float
                    
                    try:
                        node = await parent_obj.add_variable(self.namespace_idx, var_name, value, datatype=ua_type)
                        await node.set_writable()
                        
                        # Store node with path
                        if parent_obj == lift_obj:
                            node_path = var_name
                        elif parent_obj == station_data_obj:
                            node_path = f"StationData.{var_name}" 
                        elif parent_obj == handshake_obj:
                            node_path = f"StationData.Handshake.{var_name}"
                        
                        self.nodes[lift_id][node_path] = node
                        
                        # Also store regular variable names for backward compatibility
                        if var_name in self.lift_state[lift_id]:
                            self.nodes[lift_id][var_name] = node
                            
                        logger.info(f"  Created variable {node_path} ({ua_type.name}) for {lift_id}")
                    except Exception as e:
                        logger.error(f"Failed to create variable '{var_name}' for {lift_id}: {e}")

            # Add the ElevatorEcoSystAssignment object
            eco_assign_obj = await lift_obj.add_object(self.namespace_idx, "ElevatorEcoSystAssignment")
            eco_assign_vars = [
                ("iTaskType", 0, ua.VariantType.Int16),
                ("iOrigination", 0, ua.VariantType.Int16),
                ("iDestination", 0, ua.VariantType.Int16),
                ("xAcknowledgeMovement", False, ua.VariantType.Boolean),
                ("iCancelAssignment", 0, ua.VariantType.Int16)
            ]
            
            for var_name, default_value, ua_type in eco_assign_vars:
                try:
                    node = await eco_assign_obj.add_variable(self.namespace_idx, var_name, default_value, datatype=ua_type)
                    await node.set_writable()
                    node_path = f"ElevatorEcoSystAssignment.{var_name}"
                    self.nodes[lift_id][node_path] = node
                    logger.info(f"  Created variable {node_path} ({ua_type.name}) for {lift_id}")
                except Exception as e:
                    logger.error(f"Failed to create EcoSyst variable '{var_name}' for {lift_id}: {e}")

        logger.info("OPC UA Server Variables Initialized")

    async def _update_opc_value(self, lift_id, name, value):
        """Update OPC UA node value if it exists, and update internal state if applicable."""
        
        # Value to write to OPC (might be modified, e.g., for comment length)
        value_for_opc = value
        if name == "sSeq_Step_comment" and isinstance(value, str) and len(value) > 200:
            value_for_opc = value[:200]

        opc_node_exists = lift_id in self.nodes and name in self.nodes[lift_id]
        internal_state_exists = lift_id in self.lift_state and name in self.lift_state[lift_id]

        if opc_node_exists:
            node = self.nodes[lift_id][name]
            try:
                await node.write_value(value_for_opc)
                # logger.debug(f"OPC Write: {lift_id}/{name} = {value_for_opc}")
            except Exception as e:
                logger.error(f"Failed to write OPC value for {lift_id}/{name}: {e}")
        
        if internal_state_exists:
            if not name.startswith('_'): # Standard cached variable
                self.lift_state[lift_id][name] = value # Use original value for internal state
            else: # Internal simulation variable (e.g., _sub_engine_moving)
                self.lift_state[lift_id][name] = value
            # logger.debug(f"Internal State Update: {lift_id}/{name} = {value}")
        
        if not opc_node_exists and not internal_state_exists:
            logger.warning(f"Attempted to update variable not found in OPC nodes or internal state: {lift_id}/{name}")

    async def _read_opc_value(self, lift_id, name):
        """Read value from OPC UA node"""
        # Handle system variables separately
        if lift_id == 'System':
            if lift_id in self.nodes and name in self.nodes[lift_id]:
                try:
                    value = await self.nodes[lift_id][name].read_value()
                    return value
                except Exception as e:
                    logger.error(f"Failed to read System OPC value for {name}: {e}")
                    return self.system_state.get(name, 0)
            return self.system_state.get(name, 0)
            
        # For input vars from EcoSystem, read from OPC
        is_input_from_eco = name == "xClearError" or \
                            name.startswith("ElevatorEcoSystAssignment.")
                            # Removed: name.startswith("Eco_") 
                            # Removed: name == "EcoAck_xAcknowldeFromEco"

        # For non-input vars, return cached state
        if not is_input_from_eco:
            return self.lift_state[lift_id].get(name)

        # For input vars, read from OPC
        if lift_id in self.nodes and name in self.nodes[lift_id]:
            node = self.nodes[lift_id][name]
            try:
                value = await node.read_value()
                # Update cache
                if name in self.lift_state[lift_id]:
                    self.lift_state[lift_id][name] = value
                return value
            except Exception as e:
                logger.error(f"Failed to read OPC value for {lift_id}/{name}: {e}")
                return self.lift_state[lift_id].get(name)
        else:
            return self.lift_state[lift_id].get(name)

    async def _simulate_sub_movement(self, lift_id):
        """Simulate the progress of engine or fork movement"""
        state = self.lift_state[lift_id]
        now = time.time()

        # Simulate engine movement
        if state["_sub_engine_moving"]:
            if now - state["_move_start_time"] >= self._task_duration:
                logger.info(f"[{lift_id}] Engine movement finished. Reached: {state['_move_target_pos']}")
                await self._update_opc_value(lift_id, "iElevatorRowLocation", state["_move_target_pos"])
                state["_sub_engine_moving"] = False
                return True
                
        # Simulate Fork Movement
        elif state["_sub_fork_moving"]:
            if now - state["_fork_start_time"] >= (self._task_duration / 2.0):
                logger.info(f"[{lift_id}] Fork movement finished. Reached: {state['_fork_target_pos']}")
                await self._update_opc_value(lift_id, "iCurrentForkSide", state["_fork_target_pos"])
                state["_sub_fork_moving"] = False
                # Return True because a movement just finished in this cycle
                return True # Corrected: was missing this return True, causing potential issues
                  # Return True if any sub-function is still busy
        return state["_sub_engine_moving"] or state["_sub_fork_moving"]
        
    def _calculate_movement_range(self, current_pos, *positions):
        """
        Calculate the range of positions a lift will cover during its movement.
        Returns a tuple (min_pos, max_pos) representing the range.
        
        Args:
            current_pos: Current position of the lift
            *positions: Target positions the lift will visit (origin, destination)
        """
        all_positions = [current_pos] + list(positions)
        # Filter out zero positions (invalid/unspecified positions)
        valid_positions = [pos for pos in all_positions if pos > 0]
        
        if not valid_positions:
            return (0, 0)  # No valid positions
            
        return (min(valid_positions), max(valid_positions))
    
    def _check_lift_ranges_overlap(self, my_range, other_range):
        """
        Check if two lift movement ranges overlap.
        A range of (0,0) from _calculate_movement_range indicates no valid positions for movement.
        """
        my_min, my_max = my_range
        other_min_planned, other_max_planned = other_range

        # If my lift has no valid planned movement for the current job (e.g., job is to/from invalid positions),
        # it cannot cause a collision by this specific movement command.
        if my_min == 0 and my_max == 0:
            logger.debug(f"Collision check: My lift has no effective planned movement ({my_range}). No collision for this job.")
            return False

        # If the other lift's range is (0,0), it means it's not occupying any valid positive space
        # according to _calculate_movement_range (which filters for pos > 0).
        # The standard overlap check below correctly handles this:
        # e.g., my_range=(10,20), other_range=(0,0) -> not (20 < 0 or 10 > 0) -> not (False or True) -> False.
        
        logger.debug(f"Collision check: My lift's planned path {my_range}, Other lift's occupied/planned path {other_range}.")

        # Standard range overlap check: Overlap exists if they are NOT separated.
        # NOT (my_max is to the left of other_min OR my_min is to the right of other_max)
        overlap = not (my_max < other_min_planned or my_min > other_max_planned)

        if overlap:
            logger.warning(f"COLLISION DETECTED: My lift's planned path {my_range} overlaps with other lift's path/position {other_range}.")
        return overlap
    
    async def _process_lift_logic(self, lift_id):
        """Process the PLC logic for a given lift"""
        state = self.lift_state[lift_id]
        other_lift_id = LIFT2_ID if lift_id == LIFT1_ID else LIFT1_ID
        
        # Removed call to _sync_interface_variables
        # await self._sync_interface_variables(lift_id)
        
        # Process any running sub-movements
        is_busy = await self._simulate_sub_movement(lift_id)
        if is_busy:
            return  # Don't proceed with state machine if sub-movement is in progress
        
        # Current state and prepare for transition
        current_cycle = state["iCycle"]
        step_comment = f"Cycle {current_cycle}"  # Default comment
        next_cycle = current_cycle  # Default to stay in state
        
        # Read inputs from OPC UA using the new ElevatorEcoSystAssignment variables
        task_type = await self._read_opc_value(lift_id, "ElevatorEcoSystAssignment.iTaskType")
        origination = await self._read_opc_value(lift_id, "ElevatorEcoSystAssignment.iOrigination")
        destination = await self._read_opc_value(lift_id, "ElevatorEcoSystAssignment.iDestination")
        acknowledge_movement = await self._read_opc_value(lift_id, "ElevatorEcoSystAssignment.xAcknowledgeMovement")
        cancel_assignment_request = await self._read_opc_value(lift_id, "ElevatorEcoSystAssignment.iCancelAssignment")


        # Read the system-level xWatchDog signal from the EcoSystem
        ecosystem_watchdog_status = await self._read_opc_value('System', "xWatchDog")
        # Store it or use it as needed, for example, log if it's false for a while
        
        if ecosystem_watchdog_status is False:
            logger.info(f"[PLC-SIM] Received EcoSystem xWatchDog status: {ecosystem_watchdog_status}") 
        elif ecosystem_watchdog_status is True:
            logger.debug(f"[PLC-SIM] EcoSystem xWatchDog status is True")
        else:
            logger.warning(f"[{lift_id}] Could not read system-level EcoSystem xWatchDog status.")

        # Check for error clearing requests
        
        logger.debug(f"[{lift_id}] Cycle={current_cycle}, Job: Type={task_type}, Origin={origination}, Dest={destination}")
        
        # --- Main State Machine Logic ---
        if current_cycle == -10:  # Init
            step_comment = "Initializing PLC and Subsystems"
            next_cycle = 0
            
        elif current_cycle == 0:  # Idle
            step_comment = "Idle - Waiting for Enable"
            next_cycle = 10
            
        elif current_cycle == 10:  # Ready
            step_comment = "Ready - Waiting for Job Assignment"
            
            # Update station status to OK when ready
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            
            # Clear any previous assignments
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", 0)
            
            # Check for new job
            if task_type is not None and task_type > 0:
                # Reset request values to prevent auto-repeat of tasks
                # Store in temporary variables first
                temp_task_type = task_type
                temp_origin = origination
                temp_dest = destination
                
                # Immediately clear the request values from ElevatorEcoSystAssignment
                logger.info(f"[{lift_id}] Clearing job request from OPC: TaskType, Origination, Destination")
                await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iTaskType", 0)
                await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iOrigination", 0)
                await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iDestination", 0)
                
                # Check if we're in auto-tasking mode or it's a manual request
                if not AUTO_TASKING_ENABLED:
                    # Log current status of both lifts
                    lift1_pos = self.lift_state[LIFT1_ID]['iElevatorRowLocation']
                    lift2_pos = self.lift_state[LIFT2_ID]['iElevatorRowLocation']
                    
                    logger.info(f"[JOB_START] New job for {lift_id}:")
                    logger.info(f"[JOB_START] Lift1: Position={lift1_pos}, Lift2: Position={lift2_pos}")
                    
                    # Copy job details to active assignment
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", temp_task_type)
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", temp_origin)
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", temp_dest)
                    
                    logger.info(f"[{lift_id}] Received Job: Type={temp_task_type}, Origin={temp_origin}, Dest={temp_dest}")
                    next_cycle = 25  # Go to validation step
        elif current_cycle == 25:  # Validate Assignment
            step_comment = "Validating Job Assignment"
            task_type = state["ActiveElevatorAssignment_iTaskType"]
            origin = state["ActiveElevatorAssignment_iOrigination"]
            destination = state["ActiveElevatorAssignment_iDestination"]

            # Update station status to show we're processing
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_NOTIFICATION)
                      
            # Advanced validation with collision detection
            valid = True
            error_msg = ""
            
            # Check if task type is valid
            if task_type not in [FullAssignment, MoveToAssignment, PreparePickUp, BringAway]:
                valid = False
                error_msg = f"Invalid task type: {task_type}"
                # Removed: await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                # Removed: await self._update_opc_value(lift_id, "iErrorCode", 100)
            
            # Check if destination/origin is valid
            elif task_type == FullAssignment and (destination <= 0 or origin <= 0):
                valid = False
                error_msg = "Invalid destination or origin for FullAssignment"
                # Removed: await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                # Removed: await self._update_opc_value(lift_id, "iErrorCode", 101)
                # Cancel reason will be set in the common block below
            
            # Collision detection: Calculate lift range and check for overlap with other lift
            if valid: # Only check collision if other checks passed
                current_pos = state["iElevatorRowLocation"] # My current position

                # Calculate the range THIS lift (lift_id) will cover for the current job
                # 'origin' and 'destination' are from state["ActiveElevatorAssignment_..."] for the current lift
                my_range = (0, 0) # Default to no movement if task type is not recognized for path calculation
                if task_type == FullAssignment:
                    # Path: current_pos -> origin -> destination
                    my_range = self._calculate_movement_range(current_pos, origin, destination)
                elif task_type == MoveToAssignment:
                    # Path: current_pos -> destination
                    my_range = self._calculate_movement_range(current_pos, destination)
                elif task_type == PreparePickUp:
                    # Path: current_pos -> origin
                    my_range = self._calculate_movement_range(current_pos, origin)
                elif task_type == BringAway:
                    # Path: current_pos -> destination (origin is implicitly current_pos as it has the tray)
                    my_range = self._calculate_movement_range(current_pos, destination)

                # Determine the space occupied by the OTHER lift
                other_lift_s = self.lift_state[other_lift_id]
                other_lift_current_pos = other_lift_s["iElevatorRowLocation"]
                other_lift_is_active = other_lift_s["ActiveElevatorAssignment_iTaskType"] > 0
                
                other_lift_occupied_path_range = (0, 0) # Default: other lift occupies no specific path/valid positive space

                if other_lift_is_active:
                    other_lift_task_type = other_lift_s["ActiveElevatorAssignment_iTaskType"]
                    other_lift_origin = other_lift_s["ActiveElevatorAssignment_iOrigination"]
                    other_lift_destination = other_lift_s["ActiveElevatorAssignment_iDestination"]
                    
                    log_msg_prefix = f"[{lift_id}] Collision check against {other_lift_id}:"
                    if other_lift_task_type == FullAssignment:
                        logger.debug(f"{log_msg_prefix} Other lift active with FullAssignment (pos:{other_lift_current_pos}, origin:{other_lift_origin}, dest:{other_lift_destination}). Calculating its full path.")
                        other_lift_occupied_path_range = self._calculate_movement_range(other_lift_current_pos, other_lift_origin, other_lift_destination)
                    elif other_lift_task_type == MoveToAssignment:
                        logger.debug(f"{log_msg_prefix} Other lift active with MoveToAssignment (pos:{other_lift_current_pos}, dest:{other_lift_destination}). Calculating its path.")
                        other_lift_occupied_path_range = self._calculate_movement_range(other_lift_current_pos, other_lift_destination)
                    elif other_lift_task_type == PreparePickUp:
                        logger.debug(f"{log_msg_prefix} Other lift active with PreparePickUp (pos:{other_lift_current_pos}, origin:{other_lift_origin}). Calculating its path.")
                        other_lift_occupied_path_range = self._calculate_movement_range(other_lift_current_pos, other_lift_origin)
                    elif other_lift_task_type == BringAway:
                        logger.debug(f"{log_msg_prefix} Other lift active with BringAway (pos:{other_lift_current_pos}, dest:{other_lift_destination}). Calculating its path.")
                        other_lift_occupied_path_range = self._calculate_movement_range(other_lift_current_pos, other_lift_destination)
                    else:
                        # For other unhandled active task types, consider its current position only.
                        logger.debug(f"{log_msg_prefix} Other lift active with task type {other_lift_task_type} (pos:{other_lift_current_pos}). Using its current position for collision space.")
                        other_lift_occupied_path_range = self._calculate_movement_range(other_lift_current_pos)
                else:
                    # Other lift is idle, its occupied space is its current position.
                    logger.debug(f"[{lift_id}] Collision check against {other_lift_id}: Other lift is idle at {other_lift_current_pos}. Using its current position for collision space.")
                    other_lift_occupied_path_range = self._calculate_movement_range(other_lift_current_pos)
                
                # Check for overlap using the refactored _check_lift_ranges_overlap
                overlap = self._check_lift_ranges_overlap(my_range, other_lift_occupied_path_range)
                
                if overlap:
                    valid = False
                    # The detailed logger.warning is now in _check_lift_ranges_overlap.
                    # Set a comprehensive error message for the PLC state.
                    error_msg = f"Collision risk with {other_lift_id}. My path {my_range}, Other\\'s path/pos {other_lift_occupied_path_range}."
                    
                    # Removed: await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                    # Removed: await self._update_opc_value(lift_id, "iErrorCode", 102) # Collision error code
                    # Cancel reason will be set in the common block below
            
            # Process validation result
            if valid:
                logger.info(f"[{lift_id}] Assignment Validated")
                state["_current_job_valid"] = True
                await self._update_opc_value(lift_id, "iErrorCode", 0) # Clear any previous error codes
                await self._update_opc_value(lift_id, "StationData.sShortAlarmDescription", "") # Clear descriptions
                await self._update_opc_value(lift_id, "StationData.sAlarmMessage", "") 
                await self._update_opc_value(lift_id, "StationData.sAlarmSolution", "")
                await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iCancelAssignment", 0) # Clear cancel reason
                await self._update_opc_value('System', "iCancelAssignment", 0) # Clear system cancel reason
                await self._update_opc_value(lift_id, "StationData.iStationStatus", STATUS_OK) # Set station to OK
                next_cycle = 30  # Go to acceptance
            else:
                logger.error(f"[{lift_id}] Validation failed: {error_msg}")
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0) # Clear active job
                await self._update_opc_value(lift_id, "iErrorCode", 0) # No hard PLC error for rejection
                
                # Determine cancel_reason (existing logic)
                cancel_reason = CANCEL_INVALID_ASSIGNMENT # Default
                if "invalid task type" in error_msg.lower():
                    cancel_reason = CANCEL_INVALID_ASSIGNMENT
                elif "tray is on forks" in error_msg.lower(): 
                    cancel_reason = CANCEL_PICKUP_WITH_TRAY
                elif "destination out of reach" in error_msg.lower():
                    cancel_reason = CANCEL_DESTINATION_OUT_OF_REACH
                elif "origin out of reach" in error_msg.lower():
                    cancel_reason = CANCEL_ORIGIN_OUT_OF_REACH
                elif "invalid destination or origin" in error_msg.lower() or "zero" in error_msg.lower():
                    cancel_reason = CANCEL_INVALID_ZERO_POSITION
                elif "collision" in error_msg.lower() or "overlap" in error_msg.lower():
                    cancel_reason = CANCEL_LIFTS_CROSS
                
                # Update cancel reasons on OPC UA
                await self._update_opc_value('System', "iCancelAssignment", cancel_reason)
                await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iCancelAssignment", cancel_reason)
                
                # Update StationData for EcoSystem display of rejection
                await self._update_opc_value(lift_id, "StationData.sShortAlarmDescription", "Job Rejected")
                await self._update_opc_value(lift_id, "StationData.sAlarmMessage", error_msg) # Detailed reason for rejection
                await self._update_opc_value(lift_id, "StationData.sAlarmSolution", "Review job parameters or wait for other lift.")
                await self._update_opc_value(lift_id, "StationData.iStationStatus", STATUS_WARNING) # Set station to warning
                
                next_cycle = 10  # Go back to ready
                
        elif current_cycle == 30:  # Assignment Accepted
            step_comment = "Assignment Accepted - Starting Execution"
            task_type = state["ActiveElevatorAssignment_iTaskType"]
            
            if task_type == FullAssignment:
                next_cycle = 100  # Start full assignment sequence
            elif task_type == MoveToAssignment:
                next_cycle = 300  # Start move-to sequence
            elif task_type == PreparePickUp:
                next_cycle = 400  # Start prepare-pickup sequence
            else:
                next_cycle = 10  # Invalid task type, go back to ready
                
        elif current_cycle == 100:  # Start Full Assignment
            step_comment = "Starting Full Assignment Job"
            next_cycle = 102  # Move to origin
            
        elif current_cycle == 102:  # Move to Origin
            step_comment = "Moving to Origin Position"
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            
            # Check if already at origin
            if state["iElevatorRowLocation"] == target_loc:
                logger.info(f"[{lift_id}] Already at Origin {target_loc}")
                next_cycle = 150  # Skip move, go to fork preparation
            else:
                # Start move to origin
                state["_move_target_pos"] = target_loc
                state["_move_start_time"] = time.time()
                state["_sub_engine_moving"] = True
                logger.info(f"[{lift_id}] Moving to Origin {target_loc}")
                # Will stay in this state until movement completes
                
        elif current_cycle == 150:  # Prepare Forks for Pickup
            step_comment = "Preparing Forks for Pickup"
            origin = state["ActiveElevatorAssignment_iOrigination"]
            
            # Determine fork side based on origin position
            target_fork_side = RobotSide if origin < 50 else OpperatorSide
            
            # Check if forks already at correct side
            if state["iCurrentForkSide"] == target_fork_side:
                logger.info(f"[{lift_id}] Forks already at correct side for pickup")
                next_cycle = 155  # Skip fork movement
            else:
                # Start fork movement
                state["_fork_target_pos"] = target_fork_side
                state["_fork_start_time"] = time.time()
                state["_sub_fork_moving"] = True
                logger.info(f"[{lift_id}] Moving forks to {target_fork_side} for pickup")
                # Will stay in this state until movement completes
                
        elif current_cycle == 155:  # Pickup
            step_comment = "Picking Up Load"
            
            # Simulate pickup action
            await self._update_opc_value(lift_id, "xTrayInElevator", True)
            logger.info(f"[{lift_id}] Picked up load")
            next_cycle = 160
            
        elif current_cycle == 160:  # Move Forks to Middle
            step_comment = "Moving Forks to Middle Position"
            
            # Check if forks already at middle
            if state["iCurrentForkSide"] == MiddenLocation:
                logger.info(f"[{lift_id}] Forks already at middle position")
                next_cycle = 200  # Skip fork movement
            else:
                # Start fork movement
                state["_fork_target_pos"] = MiddenLocation
                state["_fork_start_time"] = time.time()
                state["_sub_fork_moving"] = True
                logger.info(f"[{lift_id}] Moving forks to middle position")
                # Will stay in this state until movement completes
                
        elif current_cycle == 200:  # Move to Destination
            step_comment = "Moving to Destination"
            target_loc = state["ActiveElevatorAssignment_iDestination"]
            
            # Check if already at destination
            if state["iElevatorRowLocation"] == target_loc:
                logger.info(f"[{lift_id}] Already at Destination {target_loc}")
                next_cycle = 250  # Skip move
            else:
                # Start move to destination
                state["_move_target_pos"] = target_loc
                state["_move_start_time"] = time.time()
                state["_sub_engine_moving"] = True
                logger.info(f"[{lift_id}] Moving to Destination {target_loc}")
                # Will stay in this state until movement completes
                
        elif current_cycle == 250:  # Prepare Forks for Place
            step_comment = "Preparing Forks for Place"
            destination = state["ActiveElevatorAssignment_iDestination"]
            
            # Determine fork side based on destination position
            target_fork_side = RobotSide if destination < 50 else OpperatorSide
            
            # Check if forks already at correct side
            if state["iCurrentForkSide"] == target_fork_side:
                logger.info(f"[{lift_id}] Forks already at correct side for place")
                next_cycle = 255  # Skip fork movement
            else:
                # Start fork movement
                state["_fork_target_pos"] = target_fork_side
                state["_fork_start_time"] = time.time()
                state["_sub_fork_moving"] = True
                logger.info(f"[{lift_id}] Moving forks to {target_fork_side} for place")
                # Will stay in this state until movement completes
                
        elif current_cycle == 255:  # Place
            step_comment = "Placing Load"
            
            # Simulate place action
            await self._update_opc_value(lift_id, "xTrayInElevator", False)
            logger.info(f"[{lift_id}] Placed load")
            next_cycle = 260
            
        elif current_cycle == 260:  # Move Forks to Middle
            step_comment = "Moving Forks to Middle Position"
            
            # Check if forks already at middle
            if state["iCurrentForkSide"] == MiddenLocation:
                logger.info(f"[{lift_id}] Forks already at middle position")
                next_cycle = 299  # Skip fork movement
            else:
                # Start fork movement
                state["_fork_target_pos"] = MiddenLocation
                state["_fork_start_time"] = time.time()
                state["_sub_fork_moving"] = True
                logger.info(f"[{lift_id}] Moving forks to middle position")
                # Will stay in this state until movement completes
                
        elif current_cycle == 299:  # Job Complete
            step_comment = "Job Complete - Returning to Ready"
            # Reset any cancel assignment reasons
            await self._update_opc_value('System', "iCancelAssignment", 0)
            await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iCancelAssignment", 0)
            # Set status back to OK
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            next_cycle = 10
            
        elif current_cycle == 300:  # Start MoveToAssignment
            step_comment = "Starting MoveTo Job"
            next_cycle = 301
            
        elif current_cycle == 301:  # Move to Position
            step_comment = "Moving to Requested Position"
            target_loc = state["ActiveElevatorAssignment_iDestination"]
            
            # Check if already at destination
            if state["iElevatorRowLocation"] == target_loc:
                logger.info(f"[{lift_id}] Already at Requested Position {target_loc}")
                next_cycle = 399  # Skip move
            else:
                # Start move
                state["_move_target_pos"] = target_loc
                state["_move_start_time"] = time.time()
                state["_sub_engine_moving"] = True
                logger.info(f"[{lift_id}] Moving to Requested Position {target_loc}")
                # Will stay in this state until movement completes
                
        elif current_cycle == 399:  # MoveTo Complete
            step_comment = "MoveTo Complete - Returning to Ready"
            next_cycle = 10  # Return to ready state
            
        elif current_cycle == 400:  # Start PreparePickUp
            step_comment = "Starting PreparePickUp Job"
            next_cycle = 102  # Use same move sequence as FullAssignment
            
        else:  # Unknown cycle
            step_comment = f"Unknown Cycle {current_cycle} - Resetting"
            logger.warning(f"[{lift_id}] Unknown cycle {current_cycle}. Resetting.")
            next_cycle = 10  # Go back to ready state
            
        # Update step comment and cycle if needed
        await self._update_opc_value(lift_id, "sSeq_Step_comment", step_comment)
        
        # Only change cycle if not busy with movement and next cycle is different
        if not state["_sub_engine_moving"] and not state["_sub_fork_moving"] and next_cycle != current_cycle:
            logger.info(f"[{lift_id}] Cycle transition: {current_cycle} -> {next_cycle}")
            await self._update_opc_value(lift_id, "iCycle", next_cycle)

    async def run(self):
        try:
            await self._initialize_server()
        except Exception as e:
            logger.error(f"Failed to initialize server: {e}")
            return

        async with self.server:
            logger.info("Dual Lift PLC Simulator Server Started.")
            self.running = True
            while self.running:
                try:
                    # Process logic for both lifts
                    await self._process_lift_logic(LIFT1_ID)
                    await self._process_lift_logic(LIFT2_ID)

                except Exception as e:
                    logger.exception(f"Error in main processing loop: {e}")

                await asyncio.sleep(0.2)  # PLC cycle time

    async def stop(self):
        self.running = False
        logger.info("Dual Lift PLC Simulator Stopping...")


async def main():
    logger.info("Starting PLC Simulator (Dual Lift)")
    
    # Use the global variable for auto-tasking mode
    global AUTO_TASKING_ENABLED
    # Already initialized at the top of the file
    
    try:
        # Create and run the simulator
        plc_sim = PLCSimulator_DualLift()
        
        # Add signal handlers for graceful shutdown
        try:
            # For Windows
            if sys.platform == 'win32':
                def handle_shutdown(sig, frame):
                    logger.info(f"Received signal {sig}, shutting down gracefully...")
                    asyncio.create_task(plc_sim.stop())
                    
                import signal
                signal.signal(signal.SIGINT, handle_shutdown)
                signal.signal(signal.SIGBREAK, handle_shutdown)
            else:
                # For Linux/Mac
                import signal
                def handle_shutdown(sig, frame):
                    logger.info(f"Received signal {sig}, shutting down gracefully...")
                    asyncio.create_task(plc_sim.stop())
                    
                signal.signal(signal.SIGINT, handle_shutdown)
                signal.signal(signal.SIGTERM, handle_shutdown)
        except Exception as e:
            logger.warning(f"Could not set up signal handlers: {e}")
        
        try:
            await plc_sim.run()
        except asyncio.CancelledError:
            logger.info("PLC Simulator main task was cancelled, shutting down...")
        except Exception as e:
            logger.error(f"Error in PLC Simulator main loop: {e}", exc_info=True)
        finally:
            logger.info("PLC Simulator shutting down...")
            await plc_sim.stop()
    except Exception as e:
        logger.error(f"Error starting the PLC Simulator: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application terminated by KeyboardInterrupt")
    except Exception as e:
        logger.exception(f"Unhandled exception in main: {e}")
    finally:
        logger.info("Exiting application")
