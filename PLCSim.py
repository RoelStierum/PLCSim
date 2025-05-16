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
RobotSide = 2  # Right (robot side)
OpperatorSide = 1  # Left (operator side)

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

SIMULATION_CYCLE_TIME_MS = 200  # milliseconds
FORK_MOVEMENT_DURATION_S = 1.0 # seconds
LIFT_MOVEMENT_DURATION_PER_ROW_S = 0.05 # seconds

class PLCSimulator_DualLift:
    # Fork positions (sForks_Position_*)
    sForks_Position_LEFT = 1
    sForks_Position_MIDDLE = 0
    sForks_Position_RIGHT = 2

    # Task types (iTaskType)
    TASK_TYPE_NONE = 0
    TASK_TYPE_FULL_ASSIGNMENT = 1
    TASK_TYPE_MOVE_TO = 2
    TASK_TYPE_PREPARE_PICKUP = 3
    TASK_TYPE_BRING_AWAY = 4

    def __init__(self, endpoint="opc.tcp://127.0.0.1:4860/gibas/plc/"):
        self.server = Server()
        self.endpoint = endpoint
        self.namespace_idx = None
        # self.nodes = {LIFT1_ID: {}, LIFT2_ID: {}, 'System': {}} # Replaced by opc_node_map
        self.opc_node_map = {} # Stores (lift_id_or_system_key, var_name_in_state) -> opc_node
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
            # --- Status for PlcToEco/StationData[idx]/ ---
            "iCycle": 0,
            "iStationStatus": STATUS_BOOTING,
            "sStationStateDescription": "Initializing", # Will be formed based on iCycle
            "sShortAlarmDescription": "",
            "sAlarmSolution": "",
            "iCancelAssignment": 0, # From PlcToEco.StationData[X].iCancelAssignment

            # --- Status for PlcToEco/StationData[idx]/Handshake/ ---
            "iJobType": 0,      # For Handshake.iJobType (PLC to Eco: GetTray/SetTray type)
            "iRowNr": 0,        # For Handshake.iRowNr

            # --- Status for PlcToEco/ElevatorX/ ---
            "iElevatorRowLocation": 0,
            "xTrayInElevator": False,
            "iCurrentForkSide": MiddenLocation,
            "iErrorCode": 0,
            "sSeq_Step_comment": "Initializing", # Specific to ElevatorX

            # --- Inputs from EcoToPlc/ElevatorX/ElevatorXEcoSystAssignment/ ---
            # These are read from OPC but also stored in lift_state for logic
            "Eco_iTaskType": 0,
            "Eco_iOrigination": 0,
            "Eco_iDestination": 0,
            "Eco_xAcknowledgeMovement": False,
            "Eco_iCancelAssignment": 0, # From EcoToPlc...iCancelAssignment (Eco to PLC)
            "xClearError": False, # From EcoToPlc...xClearError

            # --- Active Job (Internal processing, not directly in PlcToEco as full struct) ---
            "ActiveElevatorAssignment_iTaskType": 0,
            "ActiveElevatorAssignment_iOrigination": 0,
            "ActiveElevatorAssignment_iDestination": 0,

            # --- Simulation Internals (not directly OPC UA variables) ---
            "_watchdog_plc_state": False, # Internal PLC reaction to Eco's watchdog
            "_sub_fork_moving": False,
            "_sub_engine_moving": False,
            "_move_target_pos": 0,
            "_move_start_time": 0,
            "_fork_target_pos": MiddenLocation,
            "_fork_start_time": 0,
            "_current_job_valid": False
        }

        # System variables for GVL_OPC/PlcToEco and GVL_OPC/EcoToPlc
        self.system_state = {
            # PlcToEco
            "iAmountOfSations": len(LIFTS),
            "iMainStatus": STATUS_BOOTING,
            # EcoToPlc
            "xWatchDog": False  # EcoSystem status, written by EcoSystem, read by PLC
            # "iCancelAssignmentReson" (global) is removed as it's per station now.
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

    def _get_elevator_info(self, lift_id_key: str) -> tuple[str, int] | None:
        """Maps internal lift_id (LIFT1_ID) to OPC UA elevator name and station index."""
        if lift_id_key == LIFT1_ID:
            return "Elevator1", 0
        elif lift_id_key == LIFT2_ID:
            return "Elevator2", 1
        logger.error(f"Unknown lift_id_key: {lift_id_key}")
        return None

    async def _initialize_server(self):
        logger.info(f"Setting up dual-lift server on endpoint: {self.endpoint} with Di_Call_Blocks/OPC_UA structure")
        
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name("Gibas Dual Lift PLC Simulator (Di_Call_Blocks/OPC_UA)")
        
        uri = "http://gibas.com/plc/"
        self.namespace_idx = await self.server.register_namespace(uri)
        logger.info(f"Registered namespace '{uri}' with index: {self.namespace_idx}")
        
        base_obj = self.server.nodes.objects
        di_call_blocks_obj = await base_obj.add_object(self.namespace_idx, "Di_Call_Blocks")
        opc_ua_obj = await di_call_blocks_obj.add_object(self.namespace_idx, "OPC_UA")

        plc_to_eco_obj = await opc_ua_obj.add_object(self.namespace_idx, "PlcToEco")
        eco_to_plc_obj = await opc_ua_obj.add_object(self.namespace_idx, "EcoToPlc")

        # --- Create Di_Call_Blocks/OPC_UA/PlcToEco system variables ---
        sys_plc_to_eco_vars = {
            "iAmountOfSations": self.system_state["iAmountOfSations"],
            "iMainStatus": self.system_state["iMainStatus"]
        }
        for name, value in sys_plc_to_eco_vars.items():
            ua_type = ua.VariantType.Int16 if isinstance(value, int) else ua.VariantType.String
            node = await plc_to_eco_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type)
            await node.set_writable() # Sim allows writing, real PLC might be read-only from Eco
            self.opc_node_map[("System", name)] = node
            logger.info(f"  Created Di_Call_Blocks/OPC_UA/PlcToEco/{name}")

        # --- Create Di_Call_Blocks/OPC_UA/EcoToPlc system variables ---
        eco_to_plc_sys_vars = {
            "xWatchDog": self.system_state["xWatchDog"]
        }
        for name, value in eco_to_plc_sys_vars.items():
            ua_type = ua.VariantType.Boolean if isinstance(value, bool) else ua.VariantType.String
            node = await eco_to_plc_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type)
            await node.set_writable() # EcoSystem writes to this
            self.opc_node_map[("System", name)] = node # Overwrites if name is same, ensure unique keys if needed
            logger.info(f"  Created Di_Call_Blocks/OPC_UA/EcoToPlc/{name}")

        # --- Create structure for each lift ---
        station_data_parent_obj = await plc_to_eco_obj.add_object(self.namespace_idx, "StationData")

        for lift_id_key in LIFTS: # LIFT1_ID, LIFT2_ID
            elevator_info = self._get_elevator_info(lift_id_key)
            if not elevator_info:
                continue
            elevator_name, station_idx = elevator_info

            initial_lift_state = self.lift_state[lift_id_key]
            initial_lift_state['iCycle'] = 10 # Ensure ready state
            initial_lift_state['iStationStatus'] = STATUS_OK
            initial_lift_state['sSeq_Step_comment'] = "Ready - Waiting for Job Assignment"
            initial_lift_state['sStationStateDescription'] = "Ready for Job"


            # --- Di_Call_Blocks/OPC_UA/PlcToEco/StationData[idx]/ ---
            station_idx_obj = await station_data_parent_obj.add_object(self.namespace_idx, str(station_idx))
            logger.info(f"  Created Di_Call_Blocks/OPC_UA/PlcToEco/StationData/{station_idx} for {lift_id_key}")
            
            station_vars_map = { # internal_state_key: opc_ua_type (None for auto-detect)
                "iCycle": ua.VariantType.Int16,
                "iStationStatus": ua.VariantType.Int16,
                "sStationStateDescription": ua.VariantType.String,
                "sShortAlarmDescription": ua.VariantType.String,
                "sAlarmSolution": ua.VariantType.String,
                "iCancelAssignment": ua.VariantType.Int16 # As per interface.txt (INT)
            }
            for name, ua_type_val in station_vars_map.items():
                value = initial_lift_state[name]
                node = await station_idx_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type_val)
                await node.set_writable()
                self.opc_node_map[(lift_id_key, name)] = node
                logger.info(f"    Var: {name} under StationData/{station_idx}")

            # --- Di_Call_Blocks/OPC_UA/PlcToEco/StationData[idx]/Handshake/ ---
            handshake_obj = await station_idx_obj.add_object(self.namespace_idx, "Handshake")
            handshake_vars_map = {
                "iRowNr": ua.VariantType.Int16,
                "iJobType": ua.VariantType.Int16 # Corresponds to iAssignmentType in old state
            }
            for name, ua_type_val in handshake_vars_map.items():
                value = initial_lift_state[name] # Ensure these keys exist in template
                node = await handshake_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type_val)
                await node.set_writable()
                self.opc_node_map[(lift_id_key, name)] = node
                logger.info(f"    Var: Handshake/{name} under StationData/{station_idx}")

            # --- Di_Call_Blocks/OPC_UA/PlcToEco/ElevatorX/ ---
            elevator_plc_obj = await plc_to_eco_obj.add_object(self.namespace_idx, elevator_name)
            logger.info(f"  Created Di_Call_Blocks/OPC_UA/PlcToEco/{elevator_name} for {lift_id_key}")
            elevator_vars_map = {
                "iElevatorRowLocation": ua.VariantType.Int16,
                "xTrayInElevator": ua.VariantType.Boolean,
                "iCurrentForkSide": ua.VariantType.Int16,
                "iErrorCode": ua.VariantType.Int16,
                "sSeq_Step_comment": ua.VariantType.String
            }
            for name, ua_type_val in elevator_vars_map.items():
                value = initial_lift_state[name]
                node = await elevator_plc_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type_val)
                await node.set_writable()
                self.opc_node_map[(lift_id_key, name)] = node
                logger.info(f"    Var: {name} under PlcToEco/{elevator_name}")

            # --- Di_Call_Blocks/OPC_UA/EcoToPlc/ElevatorX/ElevatorXEcoSystAssignment/ ---
            elevator_eco_obj = await eco_to_plc_obj.add_object(self.namespace_idx, elevator_name)
            assign_obj_name = f"{elevator_name}EcoSystAssignment"
            eco_assign_obj = await elevator_eco_obj.add_object(self.namespace_idx, assign_obj_name)
            logger.info(f"  Created Di_Call_Blocks/OPC_UA/EcoToPlc/{elevator_name}/{assign_obj_name} for {lift_id_key}")
            
            # Variables under Di_Call_Blocks/OPC_UA/EcoToPlc/ElevatorX/ElevatorXEcoSystAssignment/
            eco_assignment_specific_vars_map = { # internal_state_key_prefix "Eco_": opc_ua_type
                "Eco_iTaskType": (ua.VariantType.Int64, "iTaskType"), # (type, opc_name)
                "Eco_iOrigination": (ua.VariantType.Int64, "iOrigination"),
                "Eco_iDestination": (ua.VariantType.Int64, "iDestination"),
            }
            for state_key, (ua_type_val, opc_name) in eco_assignment_specific_vars_map.items():
                value = initial_lift_state[state_key]
                node = await eco_assign_obj.add_variable(self.namespace_idx, opc_name, value, datatype=ua_type_val)
                await node.set_writable() # EcoSystem writes these
                self.opc_node_map[(lift_id_key, state_key)] = node # Store with "Eco_" prefixed key
                logger.info(f"    Var: {opc_name} under EcoToPlc/{elevator_name}/{assign_obj_name}")

            # Variables directly under Di_Call_Blocks/OPC_UA/EcoToPlc/ElevatorX/
            eco_elevator_direct_vars_map = {
                "Eco_xAcknowledgeMovement": (ua.VariantType.Boolean, "xAcknowledgeMovement"),
                # OPC UA name is "iCancelAssignent" as per interface.txt (with typo)
                # Internal state key remains "Eco_iCancelAssignment"
                "Eco_iCancelAssignment": (ua.VariantType.Int64, "iCancelAssignment"), # Changed Int16 to Int64
                "xClearError": (ua.VariantType.Boolean, "xClearError") # Added for error clearing
            }
            for state_key, (ua_type_val, opc_name) in eco_elevator_direct_vars_map.items():
                value = initial_lift_state[state_key]
                # These are added to elevator_eco_obj (GVL_OPC/EcoToPlc/ElevatorX/)
                
                opc_name_to_use = opc_name # Default OPC name from map
                if state_key == "Eco_iCancelAssignment":
                    if lift_id_key == LIFT1_ID:
                        opc_name_to_use = "iCancelAssignent"  # Typo for LIFT1_ID
                    # For LIFT2_ID, opc_name_to_use remains "iCancelAssignment" (correct)

                node = await elevator_eco_obj.add_variable(self.namespace_idx, opc_name_to_use, value, datatype=ua_type_val) 
                await node.set_writable()
                self.opc_node_map[(lift_id_key, state_key)] = node
                logger.info(f"    Var: {opc_name_to_use} under EcoToPlc/{elevator_name}")
        
        logger.info("OPC UA Server Variables Initialized with Di_Call_Blocks/OPC_UA structure")

    async def _update_opc_value(self, lift_id_or_system_key, state_var_name, value):
        """Update OPC UA node value using the opc_node_map and update internal state if applicable."""
        
        value_for_opc = value
        # Apply transformations if necessary (e.g., string length)
        if state_var_name == "sSeq_Step_comment" and isinstance(value, str) and len(value) > 200:
            value_for_opc = value[:200]
        elif state_var_name == "sStationStateDescription" and isinstance(value, str) and len(value) > 200: # Assuming similar limit
            value_for_opc = value[:200]


        node_key = (lift_id_or_system_key, state_var_name)
        node = self.opc_node_map.get(node_key)

        if node:
            try:
                current_opc_val = await node.read_value()
                if current_opc_val != value_for_opc: # Write only if value changed
                    await node.write_value(value_for_opc)
                    # logger.debug(f"OPC Write: {node_key} = {value_for_opc}")
            except Exception as e:
                logger.error(f"Failed to write OPC value for {node_key}: {e}")
        # else:
            # logger.warning(f"OPC Node not found for {node_key} during update.")

        # Update internal state
        if lift_id_or_system_key == "System":
            if state_var_name in self.system_state:
                self.system_state[state_var_name] = value
        elif lift_id_or_system_key in self.lift_state:
            if state_var_name in self.lift_state[lift_id_or_system_key]:
                self.lift_state[lift_id_or_system_key][state_var_name] = value
            # else:
                # logger.warning(f"Internal state key {state_var_name} not found for {lift_id_or_system_key}")
        # else:
            # logger.warning(f"Lift ID/System key {lift_id_or_system_key} not found for internal state update.")


    async def _read_opc_value(self, lift_id_or_system_key, state_var_name):
        """Read value from OPC UA node using opc_node_map, and update internal cache for inputs."""
        node_key = (lift_id_or_system_key, state_var_name)
        node = self.opc_node_map.get(node_key)

        if node:
            try:
                value = await node.read_value()
                # If it's an input variable (Eco_ prefixed, or xWatchDog), update internal state cache
                if state_var_name.startswith("Eco_") or \
                   (lift_id_or_system_key == "System" and state_var_name == "xWatchDog"):
                    
                    if lift_id_or_system_key == "System":
                        if state_var_name in self.system_state:
                            self.system_state[state_var_name] = value
                    elif lift_id_or_system_key in self.lift_state:
                        if state_var_name in self.lift_state[lift_id_or_system_key]:
                            self.lift_state[lift_id_or_system_key][state_var_name] = value
                return value
            except Exception as e:
                logger.error(f"Failed to read OPC value for {node_key}: {e}")
        # else:
            # logger.warning(f"OPC Node not found for {node_key} during read.")

        # Fallback to internal state if OPC read fails or node not found
        if lift_id_or_system_key == "System":
            return self.system_state.get(state_var_name)
        elif lift_id_or_system_key in self.lift_state:
            return self.lift_state[lift_id_or_system_key].get(state_var_name)
        
        return None # Should not happen if logic is correct

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
            if now - state["_fork_start_time"] >= (self._task_duration / 2.0): # Corrected to use FORK_MOVEMENT_DURATION_S or similar
                logger.info(f"[{lift_id}] Fork movement finished. Reached: {state['_fork_target_pos']}")
                await self._update_opc_value(lift_id, "iCurrentForkSide", state["_fork_target_pos"])
                state["_sub_fork_moving"] = False
                return True # Ensure this returns True when movement finishes in this cycle
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

        # Synchronize xTrayInElevator from OPC node to internal state at the beginning of each cycle
        # This allows EcoSystemSim's "Toggle Tray" to correctly update the PLC's understanding
        # The state_var_name for xTrayInElevator in opc_node_map is "xTrayInElevator"
        node_key_tray_status = (lift_id, "xTrayInElevator")
        # Corrected: Use self.opc_node_map instead of self.nodes
        if node_key_tray_status in self.opc_node_map:
            try:
                # Use _read_opc_value which also updates internal cache if it's an input
                # However, xTrayInElevator is an output from PLC, so direct read from node is fine here for sync
                # For consistency and to ensure the value is fresh from OPC for this specific sync, read directly.
                opc_node_tray_status = self.opc_node_map[node_key_tray_status]
                current_tray_status_opc = await opc_node_tray_status.read_value()
                
                # Update internal state if different
                if self.lift_state[lift_id]["xTrayInElevator"] != current_tray_status_opc:
                    self.lift_state[lift_id]["xTrayInElevator"] = current_tray_status_opc
                    logger.info(f"[{lift_id}] Synced xTrayInElevator from OPC: {current_tray_status_opc}") # Changed self.logger to logger

            except Exception as e:
                logger.error(f"[{lift_id}] Error syncing xTrayInElevator from OPC: {e}") # Changed self.logger to logger
        else:
            logger.warning(f"[{lift_id}] OPC node for xTrayInElevator not found in opc_node_map.") # Changed self.logger to logger

        # Read current state for the lift
        current_state = self.lift_state[lift_id]

        # Process any running sub-movements
        # _simulate_sub_movement will return True if a movement is *still* in progress.
        # If a movement *just finished* in this call, it will have updated state (e.g., iElevatorRowLocation)
        # and its respective flag (e.g., _sub_engine_moving) will now be False.
        still_busy_with_sub_movement = await self._simulate_sub_movement(lift_id)
        
        # If a sub-movement is *actively* running (not just completed in the above call),
        # then we should wait for it to finish before processing main cycle logic.
        if still_busy_with_sub_movement:
            # logger.debug(f"[{lift_id}] Sub-movement still in progress. Waiting.")
            return 
        
        current_cycle = state["iCycle"]
        step_comment = f"Cycle {current_cycle}"  # Default comment
        next_cycle = current_cycle
        
        task_type = await self._read_opc_value(lift_id, "Eco_iTaskType")
        origination = await self._read_opc_value(lift_id, "Eco_iOrigination")
        destination = await self._read_opc_value(lift_id, "Eco_iDestination")
        acknowledge_movement = await self._read_opc_value(lift_id, "Eco_xAcknowledgeMovement")
        cancel_assignment_reason_from_eco = await self._read_opc_value(lift_id, "Eco_iCancelAssignment")


        # Read the system-level xWatchDog signal from the EcoSystem
        ecosystem_watchdog_status = await self._read_opc_value('System', "xWatchDog")
        
        # Handle xWatchDog
        if ecosystem_watchdog_status is False:
            # logger.warning(f"[{lift_id}] EcoSystem Watchdog is FALSE.") # Potentially log periodically
            state["_watchdog_plc_state"] = False # Update internal PLC watchdog state
        elif ecosystem_watchdog_status is True:
            # logger.info(f"[{lift_id}] EcoSystem Watchdog is TRUE. Acknowledging.")
            await self._update_opc_value('System', "xWatchDog", False) # PLC acknowledges watchdog by setting it back to False
            state["_watchdog_plc_state"] = True # Internal PLC watchdog status
        else:
            logger.warning(f"[{lift_id}] EcoSystem Watchdog returned unexpected value: {ecosystem_watchdog_status}")


        # Check for error clearing requests
        clear_error_request = await self._read_opc_value(lift_id, "xClearError") # Read xClearError
        if clear_error_request and state["iErrorCode"] != 0:
            logger.info(f"[{lift_id}] Received xClearError request. Clearing error {state['iErrorCode']}.")
            await self._update_opc_value(lift_id, "iErrorCode", 0)
            await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
            # await self._update_opc_value(lift_id, "sAlarmMessage", "") # Assuming AlarmMessage is also cleared
            await self._update_opc_value(lift_id, "sAlarmSolution", "")
            await self._update_opc_value(lift_id, "xClearError", False) # Consume the signal
            state["iErrorCode"] = 0 # Update internal state
            if current_cycle >= 800: 
                 next_cycle = 0 
            # else: # If error occurred mid-task, specific recovery might be needed.
                  # For now, just clearing and going to idle or allowing current cycle to re-evaluate.
                  # If in a task cycle, it might retry or go to a safe state.
                  # If in cycle 10, it will become ready for a new job.
            logger.info(f"[{lift_id}] Error cleared. Current cycle {current_cycle}, next cycle will be {next_cycle}")


        logger.debug(f"[{lift_id}] Cycle={current_cycle}, Job: Type={task_type}, Origin={origination}, Dest={destination}, Ack={acknowledge_movement}, ErrorCode={state['iErrorCode']}")
        
        # --- Main State Machine Logic ---
        if current_cycle == -10: # Software Init
            step_comment = "Initializing PLC and Subsystems"
            next_cycle = 0
            
        elif current_cycle == 0: # Idle / Ready for EcoSystem instructions
            step_comment = "Idle - Waiting for Enable"
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK) # Ensure OK status in Idle
            next_cycle = 10
            
        elif current_cycle == 10: # Ready for new job from EcoSystem
            step_comment = "Ready for EcoSystem job"
            if state["iErrorCode"] == 0:
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            # state["_current_job_valid"] = False # This will be set true only if a job is accepted

            # Check for new job if idle and no error
            if task_type > 0 and state["iErrorCode"] == 0:
                logger.info(f"[{lift_id}] Received new job in Cycle 10: Type={task_type}, Origin={origination}, Dest={destination}")
                
                is_job_acceptable = True
                rejection_code = 0
                rejection_msg = ""
                my_movement_range_for_collision_check = (0,0)
                
                # Basic parameter validation
                if task_type == FullAssignment: # TaskType 1
                    if not (origination > 0 and destination > 0):
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid origin/destination for FullAssignment"
                    else:
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination, destination)
                elif task_type == MoveToAssignment: # TaskType 2
                    # For MoveTo, the target is specified by iOrigination from EcoSystem
                    if not (origination > 0): # VALIDATION: Check iOrigination (which is the target)
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid origin for MoveTo" # Message updated: origin is the target
                    else:
                        # Collision check uses iOrigination as the target
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination)
                elif task_type == PreparePickUp: # TaskType 3
                    if not (origination > 0):
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid origin for PreparePickUp"
                    else:
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination)
                elif task_type == BringAway: # TaskType 4
                    if not state["xTrayInElevator"]:
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ASSIGNMENT 
                        rejection_msg = "No tray in elevator for BringAway"
                    elif not (destination > 0):
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid destination for BringAway"
                    else:
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], destination)
                else: # Unknown task type
                    is_job_acceptable = False
                    rejection_code = CANCEL_INVALID_ASSIGNMENT
                    rejection_msg = f"Unknown task type: {task_type}"
                
                # Collision Check (if basic parameters are acceptable)
                if is_job_acceptable:
                    other_lift_state = self.lift_state[other_lift_id]
                    other_lift_active_task_type = await self._read_opc_value(other_lift_id, "ActiveElevatorAssignment_iTaskType")
                    other_lift_active_origin = await self._read_opc_value(other_lift_id, "ActiveElevatorAssignment_iOrigination")
                    other_lift_active_dest = await self._read_opc_value(other_lift_id, "ActiveElevatorAssignment_iDestination")

                    if other_lift_state["_current_job_valid"] and other_lift_active_task_type > 0:
                        if other_lift_active_task_type == FullAssignment:
                             other_lift_movement_range = self._calculate_movement_range(other_lift_state["iElevatorRowLocation"], other_lift_active_origin, other_lift_active_dest)
                        elif other_lift_active_task_type == MoveToAssignment:
                             # Corrected: MoveTo uses origin as its target destination from EcoSystem's perspective
                             other_lift_movement_range = self._calculate_movement_range(other_lift_state["iElevatorRowLocation"], other_lift_active_origin)
                        elif other_lift_active_task_type == PreparePickUp:
                             other_lift_movement_range = self._calculate_movement_range(other_lift_state["iElevatorRowLocation"], other_lift_active_origin)
                        elif other_lift_active_task_type == BringAway: 
                             other_lift_movement_range = self._calculate_movement_range(other_lift_state["iElevatorRowLocation"], other_lift_active_dest)
                        else: 
                            other_lift_movement_range = self._calculate_movement_range(other_lift_state["iElevatorRowLocation"])
                    else: 
                        other_lift_movement_range = self._calculate_movement_range(other_lift_state["iElevatorRowLocation"])

                    collision_with_other_lift = self._check_lift_ranges_overlap(my_movement_range_for_collision_check, other_lift_movement_range)

                    if collision_with_other_lift:
                        is_job_acceptable = False
                        rejection_code = CANCEL_LIFTS_CROSS
                        rejection_msg = "Potential collision with other lift"
                        logger.warning(f"[{lift_id}] Collision detected in Cycle 10. My range: {my_movement_range_for_collision_check}, Other\\'s range: {other_lift_movement_range}")

                if is_job_acceptable:
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", task_type)
                    
                    # Store the received origination and destination from EcoSystem into PLC\'s ActiveElevatorAssignment
                    # For MoveTo, the 'origination' from EcoSystem is the target.
                    # For BringAway, the 'origination' for the PLC\'s active job is its current location.
                    
                    plc_active_origination = origination # Default, used by FullAssignment, PreparePickUp
                    plc_active_destination = destination # Default, used by FullAssignment, BringAway
                                        
                    if task_type == BringAway:
                        plc_active_origination = state["iElevatorRowLocation"] 
                        # plc_active_destination remains 'destination' from EcoSystem for BringAway
                    elif task_type == MoveToAssignment:
                        # For MoveTo, EcoSystem\'s 'iOrigination' is the target.
                        # PLC stores this target in its 'ActiveElevatorAssignment_iOrigination'.
                        # PLC\'s 'ActiveElevatorAssignment_iDestination' is not used for target by PLC, set to 0.
                        plc_active_origination = origination # This is the target for MoveTo
                        plc_active_destination = 0         # Destination not used as PLC target for MoveTo
                    
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", plc_active_origination)
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", plc_active_destination)
                    
                    state["_current_job_valid"] = True 
                    
                    await self._update_opc_value(lift_id, "iCancelAssignment", 0) # Corrected path to PlcToEco.StationData.X.iCancelAssignment
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
                    # await self._update_opc_value(lift_id, "sAlarmMessage", "")
                    await self._update_opc_value(lift_id, "sAlarmSolution", "")
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_NOTIFICATION) 

                    step_comment = f"TaskType {task_type} received (O:{origination}, D:{destination}). Proceeding to validation."
                    # All accepted jobs go to cycle 25 for further validation (or direct execution start)
                    # For simplicity, let's assume cycle 25 is a brief validation/routing step.
                    next_cycle = 25 
                else: # Job rejected in Cycle 10
                    step_comment = f"Job Rejected: {rejection_msg}"
                    logger.warning(f"[{lift_id}] Job rejected in Cycle 10. Reason Code: {rejection_code}, Message: {rejection_msg}")
                    
                    await self._update_opc_value(lift_id, "iCancelAssignment", rejection_code) # Corrected path
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment) # Use step_comment for the message
                    # REMOVED: await self._update_opc_value(lift_id, "sAlarmMessage", rejection_msg) 
                    await self._update_opc_value(lift_id, "sAlarmSolution", "Check job parameters. Clear/send new job from EcoSystem.")
                    
                    await self._update_opc_value(lift_id, "iErrorCode", 0) 
                    state["iErrorCode"] = 0 

                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0) # Clear active task
                    await self._update_opc_value(lift_id, "Eco_iTaskType", 0) # Clear EcoSystem request
                    state["_current_job_valid"] = False
                    
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_WARNING)
                    next_cycle = 10 

            elif state["iErrorCode"] != 0:
                step_comment = f"Cannot process new job, error active: {state['iErrorCode']}. Clear error first."
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                next_cycle = 10 
            # If no new job (task_type == 0) and no error, just stay in cycle 10.
        
        elif current_cycle == 25:  # Validate Assignment / Route to specific task sequence
            # This cycle now acts as a router after initial acceptance in cycle 10.
            # The _current_job_valid flag should be true if we reached here.
            if not state["_current_job_valid"]:
                logger.error(f"[{lift_id}] Reached Cycle 25 without a valid current job. This should not happen. Returning to Ready.")
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                await self._update_opc_value(lift_id, "Eco_iTaskType", 0) # Clear EcoSystem request too
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_WARNING)
                await self._update_opc_value(lift_id, "iCancelAssignment", CANCEL_INVALID_ASSIGNMENT) # Corrected path
                next_cycle = 10
            else:
                task_type = state["ActiveElevatorAssignment_iTaskType"]
                step_comment = f"Cycle 25: Routing TaskType {task_type}"
                logger.info(f"[{lift_id}] Cycle 25: Routing TaskType {task_type}. Origin: {state['ActiveElevatorAssignment_iOrigination']}, Dest: {state['ActiveElevatorAssignment_iDestination']}")
                if task_type == FullAssignment:
                    next_cycle = 90 
                elif task_type == MoveToAssignment:
                    next_cycle = 290 # Route to new handshake cycle for MoveTo
                elif task_type == PreparePickUp:
                    next_cycle = 490 # Route to new handshake cycle for PreparePickUp
                elif task_type == BringAway:
                    next_cycle = 400 
                else:
                    logger.error(f"[{lift_id}] Invalid task type {task_type} encountered in Cycle 25. Resetting job.")
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                    await self._update_opc_value(lift_id, "Eco_iTaskType", 0) # Corrected
                    state["_current_job_valid"] = False
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", "Internal Error: Invalid Task Route")
                    await self._update_opc_value(lift_id, "iCancelAssignment", CANCEL_INVALID_ASSIGNMENT) # Corrected path
                    next_cycle = 10 # Back to ready
        
        elif current_cycle == 90: # FullAssignment: Signal Job Acceptance to EcoSystem
            step_comment = f"FullAssignment: Job accepted. Signaling EcoSystem for origin {state['ActiveElevatorAssignment_iOrigination']}."
            logger.info(f"[{lift_id}] {step_comment}")
            await self._update_opc_value(lift_id, "iJobType", FullAssignment) # Corrected key for opc_node_map
            await self._update_opc_value(lift_id, "iRowNr", state["ActiveElevatorAssignment_iOrigination"]) # Corrected key
            next_cycle = 95

        elif current_cycle == 95: # FullAssignment: Wait for EcoSystem Acknowledge for Job Start
            step_comment = f"FullAssignment: Waiting for acknowledge from EcoSystem for origin {state['ActiveElevatorAssignment_iOrigination']}."
            ack_received = await self._read_opc_value(lift_id, "Eco_xAcknowledgeMovement")
            logger.debug(f"[{lift_id}] Cycle 95: Reading xAcknowledgeMovement for origin {state['ActiveElevatorAssignment_iOrigination']}. Value: {ack_received}") # DEBUG LINE ADDED
            if ack_received:
                logger.info(f"[{lift_id}] FullAssignment: Acknowledge for origin {state['ActiveElevatorAssignment_iOrigination']} received. Proceeding.")
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False) # Consume ack
                await self._update_opc_value(lift_id, "iJobType", 0) # Corrected key
                await self._update_opc_value(lift_id, "iRowNr", 0) # Corrected key
                next_cycle = 100 # Proceed to actual job start
            else:
                # Stay in this cycle, waiting for xAcknowledgeMovement
                next_cycle = 95

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
            target_fork_side = OpperatorSide if origin <= 50 else RobotSide
            
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
            
        elif current_cycle == 160: # FullAssignment: Move Forks to Middle (after pickup)
            step_comment = "FullAssignment: Moving forks to middle after pickup"
            
            # Corrected condition: Initiate movement only if not already moving AND not at the target
            if state["iCurrentForkSide"] != self.sForks_Position_MIDDLE and not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = self.sForks_Position_MIDDLE
                state["_fork_start_time"] = time.time() # Corrected from _fork_move_start_time
                state["_sub_fork_moving"] = True
                logger.info(f"[{lift_id}] Moving forks to middle position")

            # Check if movement is complete and at target
            if not state["_sub_fork_moving"] and state["iCurrentForkSide"] == self.sForks_Position_MIDDLE:
                logger.info(f"[{lift_id}] Forks at middle position after pickup.")
                next_cycle = 190 # Proceed to signal for destination move
            else:
                # If still moving, or if the move hasn't started yet (and not at target), stay in 160
                next_cycle = 160

        elif current_cycle == 190: # FullAssignment: Signal Destination Move
            step_comment = f"FullAssignment: Forks at middle. Signaling move to destination {state['ActiveElevatorAssignment_iDestination']}."
            logger.info(f"[{lift_id}] {step_comment}")
            await self._update_opc_value(lift_id, "iJobType", FullAssignment) # Corrected key
            await self._update_opc_value(lift_id, "iRowNr", state["ActiveElevatorAssignment_iDestination"]) # Corrected key
            next_cycle = 195

        elif current_cycle == 195: # FullAssignment: Wait for EcoSystem Acknowledge for Destination Move
            step_comment = f"FullAssignment: Waiting for acknowledge from EcoSystem for destination {state['ActiveElevatorAssignment_iDestination']}."
            ack_received = await self._read_opc_value(lift_id, "Eco_xAcknowledgeMovement")
            logger.debug(f"[{lift_id}] Cycle 195: Reading xAcknowledgeMovement for destination {state['ActiveElevatorAssignment_iDestination']}. Value: {ack_received}") # DEBUG LINE ADDED
            if ack_received:
                logger.info(f"[{lift_id}] FullAssignment: Acknowledge for destination {state['ActiveElevatorAssignment_iDestination']} received. Proceeding.")
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False) # Consume ack
                await self._update_opc_value(lift_id, "iJobType", 0) # Corrected key
                await self._update_opc_value(lift_id, "iRowNr", 0) # Corrected key
                next_cycle = 400 # Proceed to move to destination
            else:
                # Stay in this cycle, waiting for xAcknowledgeMovement
                next_cycle = 195

        # --- MoveToAssignment Sequence (TaskType 2) ---
        # Routed from cycle 25 to 290
        elif current_cycle == 290: # MoveToAssignment: Signal Job Acceptance to EcoSystem
            # Target for MoveTo is stored in ActiveElevatorAssignment_iOrigination
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveToAssignment: Job accepted. Signaling EcoSystem for target {target_loc}."
            logger.info(f"[{lift_id}] {step_comment}")
            await self._update_opc_value(lift_id, "iJobType", MoveToAssignment)
            await self._update_opc_value(lift_id, "iRowNr", target_loc)
            next_cycle = 295

        elif current_cycle == 295: # MoveToAssignment: Wait for EcoSystem Acknowledge
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveToAssignment: Waiting for acknowledge from EcoSystem for target {target_loc}."
            ack_received = await self._read_opc_value(lift_id, "Eco_xAcknowledgeMovement")
            if ack_received:
                logger.info(f"[{lift_id}] MoveToAssignment: Acknowledge for target {target_loc} received. Proceeding.")
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False) # Consume ack
                await self._update_opc_value(lift_id, "iJobType", 0)
                await self._update_opc_value(lift_id, "iRowNr", 0)
                next_cycle = 300 # Proceed to move
            else:
                next_cycle = 295 # Stay in this cycle

        elif current_cycle == 300:  # MoveToAssignment: Move Lift to Target
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveToAssignment: Moving to target position {target_loc}" 

            if not state["_sub_engine_moving"]: 
                if state["iElevatorRowLocation"] == target_loc:
                    logger.info(f"[{lift_id}] MoveToAssignment: Arrived or already at target {target_loc}.")
                    step_comment = f"MoveToAssignment: Arrived at target {target_loc}."
                    next_cycle = 310
                else: 
                    state["_move_target_pos"] = target_loc
                    state["_move_start_time"] = time.time()
                    state["_sub_engine_moving"] = True
                    logger.info(f"[{lift_id}] MoveToAssignment: Initiating move to target {target_loc}.")
        
        elif current_cycle == 310:  # MoveToAssignment: Job Complete
            target_loc = state["ActiveElevatorAssignment_iOrigination"] 
            step_comment = f"MoveToAssignment: Job Complete. Arrived at target {target_loc}. Returning to Ready."
            logger.info(f"[{lift_id}] Job type {state['ActiveElevatorAssignment_iTaskType']} (MoveToAssignment) completed for target {target_loc}.")

            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", 0) 
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", 0) 
            state["_current_job_valid"] = False

            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iOrigination", 0)
            await self._update_opc_value(lift_id, "Eco_iDestination", 0)
            
            await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)

            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value(lift_id, "iJobType", 0) 
            await self._update_opc_value(lift_id, "iRowNr", 0)   
            
            next_cycle = 10 
        
        elif current_cycle == 400: # Start BringAway: Validate (already done in 10/25), prepare for move
            step_comment = f"BringAway: Preparing to move to destination {state['ActiveElevatorAssignment_iDestination']}"
            if not state["xTrayInElevator"]: 
                step_comment = "BringAway Error: No tray at start of sequence!"
                logger.error(f"[{lift_id}] {step_comment}")
                await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment) # Use step_comment for the message
                # REMOVED: await self._update_opc_value(lift_id, "sAlarmMessage", "TaskType BringAway started but xTrayInElevator is false.")
                await self._update_opc_value(lift_id, "sAlarmSolution", "Ensure tray is present or use a different task. Reset job.")
                await self._update_opc_value(lift_id, "iErrorCode", CANCEL_INVALID_ASSIGNMENT) 
                state["iErrorCode"] = CANCEL_INVALID_ASSIGNMENT
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                await self._update_opc_value(lift_id, "Eco_iTaskType", 0) # Corrected
                state["_current_job_valid"] = False
                next_cycle = 10 
            else:
                next_cycle = 410
        elif current_cycle == 410: # BringAway: Move to Destination
            destination_pos = state["ActiveElevatorAssignment_iDestination"]
            step_comment = f"BringAway: Moving to destination {destination_pos}"

            # Check if we need to start the movement
            if state["iElevatorRowLocation"] != destination_pos and not state["_sub_engine_moving"]:
                # Only initiate if not already at destination and no movement is currently flagged as active
                # (The _simulate_sub_movement call above would have cleared _sub_engine_moving if it just finished)
                state["_move_target_pos"] = destination_pos
                state["_move_start_time"] = time.time()
                state["_sub_engine_moving"] = True
                logger.info(f"[{lift_id}] BringAway: Initiating engine move from {state['iElevatorRowLocation']} to {destination_pos}")
                # Since we just started a movement, set step_comment and allow _simulate_sub_movement next tick to handle it
                await self._update_opc_value(lift_id, "sSeq_Step_comment", step_comment)
                # DO NOT set next_cycle here, let the movement run.
                # The next tick, _simulate_sub_movement will run, and if it's still busy, it will return early.
                # If it finishes, still_busy_with_sub_movement will be false, and this cycle logic will run again.
                return # Return to let the movement start and be processed by the next tick's _simulate_sub_movement

            # If we are here, it means either:
            # 1. Movement was already started and _sub_engine_moving is true (still_busy_with_sub_movement would have returned early)
            # 2. Movement was started, _simulate_sub_movement ran, finished it, and cleared _sub_engine_moving.
            # 3. We are already at the destination.

            if not state["_sub_engine_moving"] and state["iElevatorRowLocation"] == destination_pos:
                logger.info(f"[{lift_id}] BringAway: Engine movement to {destination_pos} complete or already there.")
                step_comment = f"BringAway: Arrived at destination {destination_pos}" # Update comment for arrival
                next_cycle = 420
            else:
                # If _sub_engine_moving is true, it means _simulate_sub_movement will handle it and return early.
                # If _sub_engine_moving is false, but we are not at destination, something is wrong or it's the first entry.
                # The logic at the start of this cycle (410) should handle initiating the move.
                # We stay in 410 if movement is supposed to be happening.
                # If movement was just initiated, we returned, so this 'else' shouldn't be hit immediately after initiation.
                next_cycle = 410
        elif current_cycle == 420: # BringAway: Arrived at Destination, Wait for Ack
            destination_pos = state["ActiveElevatorAssignment_iDestination"]
            # This state is entered when _sub_engine_moving becomes false
            # Update internal location only if it's actually changed by the movement
            if state["iElevatorRowLocation"] != destination_pos:
                 state["iElevatorRowLocation"] = destination_pos 
                 await self._update_opc_value(lift_id, "iElevatorRowLocation", destination_pos)
            
            step_comment = f"BringAway: Arrived at destination {destination_pos}" # Default comment for this cycle

            # Signal EcoSystem for acknowledgement
            await self._update_opc_value(lift_id, "iJobType", BringAway) # Corrected key; Inform EcoSystem task type being acknowledged
            await self._update_opc_value(lift_id, "iRowNr", destination_pos) # Corrected key; Inform EcoSystem row for ack

            if acknowledge_movement:
                step_comment = f"BringAway: Ack received at {destination_pos}. Preparing to place tray."
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False) # Consume ack
                await self._update_opc_value(lift_id, "iJobType", 0) # Corrected key
                await self._update_opc_value(lift_id, "iRowNr", 0) # Corrected key
                next_cycle = 430
            else:
                step_comment = f"BringAway: Waiting for acknowledge at destination {destination_pos}"
                # Stay in this cycle, waiting for xAcknowledgeMovement to become true
                next_cycle = 420 

        elif current_cycle == 430: # BringAway: Move Forks to Side for Placing
            destination_pos = state["ActiveElevatorAssignment_iDestination"]
            step_comment = f"BringAway: Moving forks to side for placing at {destination_pos}"
            target_side = RobotSide if self.get_side(destination_pos) == "robot" else OpperatorSide
            
            if state["iCurrentForkSide"] == target_side:
                logger.info(f"[{lift_id}] BringAway: Forks already at side {target_side} for placing.")
                next_cycle = 435 # Skip fork movement
            else:
                state["_fork_target_pos"] = target_side
                state["_sub_fork_moving"] = True
                # sSeq_Step_comment will be updated by the main logic before return if sub_movement is active
                next_cycle = 435 # Proceed to 435 after fork movement (or immediately if already there)

        elif current_cycle == 435: # BringAway: Forks at Side, Place Tray
            # This state is entered when _sub_fork_moving (from 430) becomes false, or if skipped
            destination_pos = state["ActiveElevatorAssignment_iDestination"]
            actual_fork_side = state["_fork_target_pos"] # This was the target for the previous movement
            
            # Update current fork side if it was moved
            if state["iCurrentForkSide"] != actual_fork_side:
                 state["iCurrentForkSide"] = actual_fork_side
                 await self._update_opc_value(lift_id, "iCurrentForkSide", actual_fork_side)

            step_comment = f"BringAway: Forks at side. Placing tray at {destination_pos}."
            logger.info(f"[{lift_id}] {step_comment}")
            
            state["xTrayInElevator"] = False # Tray placed
            await self._update_opc_value(lift_id, "xTrayInElevator", False)
            
            next_cycle = 440

        elif current_cycle == 440: # BringAway: Tray Placed, Move Fork to Middle
            step_comment = f"BringAway: Tray placed at {state['ActiveElevatorAssignment_iDestination']}. Moving fork to middle."
            
            if state["iCurrentForkSide"] == MiddenLocation:
                logger.info(f"[{lift_id}] BringAway: Forks already at middle after placing.")
                next_cycle = 450 # Skip fork movement
            else:
                state["_fork_target_pos"] = MiddenLocation
                state["_sub_fork_moving"] = True
                # sSeq_Step_comment will be updated by the main logic
                next_cycle = 450 # Proceed to 450 after fork movement

        elif current_cycle == 450: # BringAway: Fork at Middle
            # This state is entered when _sub_fork_moving (from 440) becomes false, or if skipped
            # Update current fork side if it was moved
            if state["iCurrentForkSide"] != MiddenLocation:
                state["iCurrentForkSide"] = MiddenLocation
                await self._update_opc_value(lift_id, "iCurrentForkSide", MiddenLocation)

            step_comment = f"BringAway: Fork at middle. Job sequence complete."
            next_cycle = 460
            
        elif current_cycle == 460: # BringAway: Job Complete
            step_comment = "BringAway: Job Complete - Returning to Ready"
            
            logger.info(f"[{lift_id}] Job type {state['ActiveElevatorAssignment_iTaskType']} (BringAway or FullAssignment) fully completed. Clearing active and EcoSystem job.")
            # Clear active job variables
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", 0) 
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", 0)
            state["_current_job_valid"] = False

            # Clear EcoSystem job request variables by writing 0 to the OPC UA nodes
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iOrigination", 0)
            await self._update_opc_value(lift_id, "Eco_iDestination", 0)
            
            # Also reset the acknowledge flag from EcoSystem if it was somehow left true
            await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)

            # === DIAGNOSTIC START ===
            # try:
            #     logger.info(f"[{lift_id}] DIAGNOSTIC: Reading back Eco_iTaskType after attempting to clear.")
            #     # node_task_type_path_diag = self._get_node_path(lift_id, "Eco_iTaskType") # _get_node_path is not defined
            #     # if node_task_type_path_diag: # Ensure path was found
            #     #     task_type_node_diag = self.server.get_node(node_task_type_path_diag)
            #     #     eco_task_type_diag = await task_type_node_diag.read_value()
            #     #     logger.info(f"[{lift_id}] DIAGNOSTIC: Value of Eco_iTaskType on server is now: {eco_task_type_diag}")
            #     # else:
            #     #     logger.warning(f"[{lift_id}] DIAGNOSTIC: Could not get node path for Eco_iTaskType.")
            # except Exception as e_diag:
            #     logger.error(f"[{lift_id}] DIAGNOSTIC: Error reading back Eco_iTaskType: {e_diag}")
            # === DIAGNOSTIC END ===

            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value(lift_id, "iJobType", 0) # Clear any handshake signals
            await self._update_opc_value(lift_id, "iRowNr", 0)   # Clear any handshake signals
            
            next_cycle = 10 # Back to Ready
        
        elif current_cycle == 490: # PreparePickUp: Signal Job Acceptance to EcoSystem
            step_comment = f"PreparePickUp: Job accepted. Signaling EcoSystem for origin {state['ActiveElevatorAssignment_iOrigination']}."
            logger.info(f"[{lift_id}] {step_comment}")
            await self._update_opc_value(lift_id, "iJobType", PreparePickUp)
            await self._update_opc_value(lift_id, "iRowNr", state["ActiveElevatorAssignment_iOrigination"])
            next_cycle = 495

        elif current_cycle == 495: # PreparePickUp: Wait for EcoSystem Acknowledge
            step_comment = f"PreparePickUp: Waiting for acknowledge from EcoSystem for origin {state['ActiveElevatorAssignment_iOrigination']}."
            ack_received = await self._read_opc_value(lift_id, "Eco_xAcknowledgeMovement")
            logger.debug(f"[{lift_id}] Cycle 495: Reading xAcknowledgeMovement for origin {state['ActiveElevatorAssignment_iOrigination']}. Value: {ack_received}") # DEBUG LINE ADDED
            if ack_received:
                logger.info(f"[{lift_id}] PreparePickUp: Acknowledge for origin {state['ActiveElevatorAssignment_iOrigination']} received. Proceeding.")
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False) # Consume ack
                await self._update_opc_value(lift_id, "iJobType", 0) # Corrected key
                await self._update_opc_value(lift_id, "iRowNr", 0) # Corrected key
                next_cycle = 500 # Proceed to original start of PreparePickUp sequence
            else:
                # Stay in this cycle, waiting for xAcknowledgeMovement
                next_cycle = 495

        # --- PreparePickUp Sequence (TaskType 3) ---
        elif current_cycle == 500:  # Start PreparePickUp
            origination_pos = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PreparePickUp: Starting (O:{origination_pos})"
            logger.info(f"[{lift_id}] Starting PreparePickUp job for origin {origination_pos}")
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_NOTIFICATION)
            # Validate if tray is present - PreparePickUp should not have a tray
            if state["xTrayInElevator"]:
                step_comment = "PreparePickUp Error: Tray present on elevator!" # Updated step_comment for clarity
                logger.error(f"[{lift_id}] {step_comment}")
                await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment) # Use step_comment for OPC
                # REMOVED: await self._update_opc_value(lift_id, "sAlarmMessage", "TaskType PreparePickUp started but xTrayInElevator is true.")
                await self._update_opc_value(lift_id, "sAlarmSolution", "Ensure tray is not present. Reset job.")
                await self._update_opc_value(lift_id, "iErrorCode", CANCEL_PICKUP_WITH_TRAY) 
                state["iErrorCode"] = CANCEL_PICKUP_WITH_TRAY
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                await self._update_opc_value(lift_id, "Eco_iTaskType", 0) # Corrected
                state["_current_job_valid"] = False
                next_cycle = 10 # Back to ready after error
            else:
                next_cycle = 505


        elif current_cycle == 505:  # PreparePickUp: Move to Origin
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PreparePickUp: Moving to Origin {target_loc}"
            
            if state["iElevatorRowLocation"] == target_loc:
                logger.info(f"[{lift_id}] PreparePickUp: Already at Origin {target_loc}")
                next_cycle = 510  # Go to Prepare Forks
            else:
                state["_move_target_pos"] = target_loc
                state["_move_start_time"] = time.time()
                state["_sub_engine_moving"] = True
                logger.info(f"[{lift_id}] PreparePickUp: Moving to Origin {target_loc}")
                # Stays in this cycle if _sub_engine_moving is true

        elif current_cycle == 510:  # PreparePickUp: Prepare Forks at Origin
            origin_pos = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PreparePickUp: Preparing Forks at Origin {origin_pos}"
            target_fork_side = RobotSide if self.get_side(origin_pos) == "robot" else OpperatorSide
            
            if state["iCurrentForkSide"] == target_fork_side:
                logger.info(f"[{lift_id}] PreparePickUp: Forks already at correct side ({target_fork_side}) for pickup at {origin_pos}")
                next_cycle = 515  # Go to Move Forks to Middle
            else:
                state["_fork_target_pos"] = target_fork_side
                state["_fork_start_time"] = time.time()
                state["_sub_fork_moving"] = True
                logger.info(f"[{lift_id}] PreparePickUp: Moving forks to {target_fork_side} for pickup at {origin_pos}")
                # Stays in this cycle if _sub_fork_moving is true

        elif current_cycle == 515:  # PreparePickUp: Move Forks to Middle
            step_comment = "PreparePickUp: Forks prepared. Moving forks to Middle."
            if state["iCurrentForkSide"] == MiddenLocation:
                logger.info(f"[{lift_id}] PreparePickUp: Forks already at middle position.")
                next_cycle = 520  # Go to Completion
            else:
                state["_fork_target_pos"] = MiddenLocation
                state["_fork_start_time"] = time.time()
                state["_sub_fork_moving"] = True
                logger.info(f"[{lift_id}] PreparePickUp: Moving forks to middle position.")
                # Stays in this cycle if _sub_fork_moving is true

        elif current_cycle == 520:  # PreparePickUp: Job Complete
            step_comment = "PreparePickUp: Complete - Ready at Origin, forks centered. Returning to Ready."
            logger.info(f"[{lift_id}] Job type {state['ActiveElevatorAssignment_iTaskType']} (PreparePickUp) completed. Clearing active and EcoSystem job.")

            # Clear active job variables
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", 0)
            state["_current_job_valid"] = False 

            # Clear EcoSystem job request variables
            # await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iTaskType", 0)
            # await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iOrigination", 0)
            # await self._update_opc_value(lift_id, "ElevatorEcoSystAssignment.iDestination", 0)
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iOrigination", 0)
            await self._update_opc_value(lift_id, "Eco_iDestination", 0)

            await self._update_opc_value(lift_id, "iCancelAssignment", 0)
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            next_cycle = 10
            
        elif current_cycle == 800: # General Error State
            step_comment = f"Error {state['iErrorCode']} occurred. Waiting for reset."
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
            
            # Check if EcoSystem has cleared the error
            if cancel_assignment_reason_from_eco == 0:
                logger.info(f"[{lift_id}] Error cleared by EcoSystem.")
                await self._update_opc_value(lift_id, "iErrorCode", 0)
                await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
                await self._update_opc_value(lift_id, "sAlarmMessage", "")
                await self._update_opc_value(lift_id, "sAlarmSolution", "")
                next_cycle = 0 # Go to init/idle after clearing error
            # else: Stay in error state, waiting for external intervention

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
