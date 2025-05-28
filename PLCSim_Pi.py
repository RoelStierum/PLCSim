import asyncio
import logging
from asyncua import Server, ua
import random
import time
import os
import sys

# Add GPIO imports for Raspberry Pi
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logging.warning("RPi.GPIO module not available. Physical buttons will be disabled.")

# GPIO Pin definitions
EMG_STOP_PIN = 24   # Emergency stop button
RESET_PIN = 23      # Reset button
EMG_STOP_ERROR_CODE = 888  # Custom error code for emergency stop

# Zorg dat de logs map bestaat
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Clear log file at startup
log_filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'plcsim.log')
if (os.path.exists(log_filename)):
    try:
        open(log_filename, 'w').close()
        # print(f"Cleared log file: {log_filename}") # Keep console clean
    except Exception as e:
        print(f"Warning: Could not clear log file {log_filename}: {e}")

log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=logging.INFO, # Changed to INFO for more details dev
    format=log_format,
    handlers=[
        logging.FileHandler(log_filename, mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("PLCSim_DualLift")

logging.getLogger("asyncua").setLevel(logging.ERROR)

LIFT1_ID = 'Lift1'
LIFT2_ID = 'Lift2'
LIFTS = [LIFT1_ID, LIFT2_ID]

# Task Type Constants for ActiveElevatorAssignment_iTaskType
FullAssignment = 1
MoveToAssignment = 2
PreparePickUp = 3
BringAway = 4

# NEW Handshake Job Type Constants for the global PlcToEco.StationDataToEco.ExtraData.Handshake.iJobType
HANDSHAKE_JOB_TYPE_1 = 1 # For FullAss P1, MoveTo, PreparePickUp
HANDSHAKE_JOB_TYPE_2 = 2 # For FullAss P2, BringAway
HANDSHAKE_JOB_TYPE_IDLE = 0 # For clearing handshake

# Fork Side Constants
MiddenLocation = 0
RobotSide = 2
OpperatorSide = 1

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
CANCEL_BY_ECOSYSTEM = 7

SIMULATION_CYCLE_TIME_MS = 200
FORK_MOVEMENT_DURATION_S = 1.0
LIFT_MOVEMENT_DURATION_PER_ROW_S = 0.05

class PLCSimulator_DualLift:
    sForks_Position_LEFT = 1
    sForks_Position_MIDDLE = 0
    sForks_Position_RIGHT = 2

    TASK_TYPE_NONE = 0
    TASK_TYPE_FULL_ASSIGNMENT = 1
    TASK_TYPE_MOVE_TO = 2
    TASK_TYPE_PREPARE_PICKUP = 3
    TASK_TYPE_BRING_AWAY = 4

    def __init__(self, endpoint="opc.tcp://192.168.137.2:4860/gibas/plc/"):
        self.server = Server()
        self.endpoint = endpoint
        self.namespace_idx = None
        self.opc_node_map = {}
        self.running = False
        self._task_duration = 2.0 # General simulation duration for some actions
        self._pickup_offset = 2
        self.emg_stop_active = False  # Track emergency stop state
        
        # Initialize GPIO if available
        if GPIO_AVAILABLE:
            self._setup_gpio()
        
        self.to_physical_pos = lambda pos: pos if pos <= 50 else pos - 50
        self.get_side = lambda pos: "operator" if pos <= 50 else "robot"        
        self.lift_state_template = {
            "iCycle": 0,
            "iStationStatus": STATUS_BOOTING,
            "sStationStateDescription": "Initializing",
            "sShortAlarmDescription": "",
            "sAlarmSolution": "",
            "iCancelAssignment": 0,
            "iElevatorRowLocation": 0,
            "xTrayInElevator": False,
            "iCurrentForkSide": MiddenLocation,            
            "iErrorCode": 0,
            "sSeq_Step_comment": "Initializing",
            "Eco_iTaskType": 0,
            "Eco_iOrigination": 0,
            "Eco_iDestination": 0,
            "Eco_xAcknowledgeMovement": False,
            "Eco_iCancelAssignment": 0,
            "xClearError": False,
            "ActiveElevatorAssignment_iTaskType": 0,
            "ActiveElevatorAssignment_iOrigination": 0,
            "ActiveElevatorAssignment_iDestination": 0,
            "_watchdog_plc_state": False,
            "_sub_fork_moving": False,
            "_sub_engine_moving": False,
            "_move_target_pos": 0,
            "_move_start_time": 0,
            "_fork_target_pos": MiddenLocation,
            "_fork_start_time": 0,
            "_current_job_valid": False,
            "_fork_pickup_pending": False,
            "_fork_pickup_start_time": 0,
            "_fork_release_pending": False,
            "_fork_release_start_time": 0
        }

        self.system_state = {
            "iAmountOfSations": len(LIFTS),
            "iMainStatus": STATUS_BOOTING,
            "System_Handshake_iJobType": HANDSHAKE_JOB_TYPE_IDLE,
            "System_Handshake_iRowNr": 0,
            "xWatchDog": False
        }

        self.lift_state = {
            LIFT1_ID: self.lift_state_template.copy(),
            LIFT2_ID: self.lift_state_template.copy()
        }
        
        self.lift_state[LIFT1_ID]['iElevatorRowLocation'] = 5
        self.lift_state[LIFT2_ID]['iElevatorRowLocation'] = 90
        self.lift_state[LIFT1_ID]['iCycle'] = 10
        self.lift_state[LIFT2_ID]['iCycle'] = 10

    def _get_elevator_info(self, lift_id_key: str) -> tuple[str, int] | None:
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
        
        base_obj = self.server.nodes.objects
        di_call_blocks_obj = await base_obj.add_object(self.namespace_idx, "Di_Call_Blocks")
        opc_ua_obj = await di_call_blocks_obj.add_object(self.namespace_idx, "OPC_UA")

        plc_to_eco_obj = await opc_ua_obj.add_object(self.namespace_idx, "PlcToEco")
        eco_to_plc_obj = await opc_ua_obj.add_object(self.namespace_idx, "EcoToPlc")

        station_data_to_eco_obj = await plc_to_eco_obj.add_object(self.namespace_idx, "StationDataToEco")
        
        sys_plc_to_eco_vars = {
            "iAmountOfSations": self.system_state["iAmountOfSations"],
            "iMainStatus": self.system_state["iMainStatus"]
        }
        for name, value in sys_plc_to_eco_vars.items():
            ua_type = ua.VariantType.Int16 
            node = await station_data_to_eco_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type)
            await node.set_writable() 
            self.opc_node_map[("System", name)] = node

        extra_data_obj = await station_data_to_eco_obj.add_object(self.namespace_idx, "ExtraData")
        global_handshake_obj = await extra_data_obj.add_object(self.namespace_idx, "Handshake")

        global_handshake_vars_map = {
            "System_Handshake_iJobType": (self.system_state["System_Handshake_iJobType"], ua.VariantType.Int16, "iJobType"),
            "System_Handshake_iRowNr": (self.system_state["System_Handshake_iRowNr"], ua.VariantType.Int16, "iRowNr")
        }
        for state_key, (initial_value, ua_type_val, opc_name) in global_handshake_vars_map.items():
            node = await global_handshake_obj.add_variable(self.namespace_idx, opc_name, initial_value, datatype=ua_type_val)
            await node.set_writable() 
            self.opc_node_map[("System", state_key)] = node
            logger.info(f"    Created Di_Call_Blocks/OPC_UA/PlcToEco/StationDataToEco/ExtraData/Handshake/{opc_name}")

        eco_to_plc_sys_vars = { "xWatchDog": self.system_state["xWatchDog"] }
        for name, value in eco_to_plc_sys_vars.items():
            ua_type = ua.VariantType.Boolean
            node = await eco_to_plc_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type)
            await node.set_writable()
            self.opc_node_map[("System", name)] = node

        station_data_parent_obj = await plc_to_eco_obj.add_object(self.namespace_idx, "StationData")

        for lift_id_key in LIFTS:
            elevator_info = self._get_elevator_info(lift_id_key)
            if not elevator_info: continue
            elevator_name, station_idx = elevator_info
            initial_lift_state = self.lift_state[lift_id_key]
            initial_lift_state.update({
                'iCycle': 10, 'iStationStatus': STATUS_OK,
                'sSeq_Step_comment': "Ready - Waiting for Job Assignment",
                'sStationStateDescription': "Ready for Job"
            })

            station_idx_obj = await station_data_parent_obj.add_object(self.namespace_idx, str(station_idx))
            station_vars_map = {
                "iCycle": ua.VariantType.Int16, "iStationStatus": ua.VariantType.Int16,
                "sStationStateDescription": ua.VariantType.String,
                "sShortAlarmDescription": ua.VariantType.String,
                "sAlarmSolution": ua.VariantType.String,
                "iCancelAssignment": ua.VariantType.Int16
            }
            for name, ua_type_val in station_vars_map.items():
                node = await station_idx_obj.add_variable(self.namespace_idx, name, initial_lift_state[name], datatype=ua_type_val)
                await node.set_writable()
                self.opc_node_map[(lift_id_key, name)] = node
            
            elevator_plc_obj = await plc_to_eco_obj.add_object(self.namespace_idx, elevator_name)
            elevator_vars_map = {
                "iElevatorRowLocation": ua.VariantType.Int16, "xTrayInElevator": ua.VariantType.Boolean,
                "iCurrentForkSide": ua.VariantType.Int16, "iErrorCode": ua.VariantType.Int16,
                "sSeq_Step_comment": ua.VariantType.String
            }
            for name, ua_type_val in elevator_vars_map.items():
                node = await elevator_plc_obj.add_variable(self.namespace_idx, name, initial_lift_state[name], datatype=ua_type_val)
                await node.set_writable()
                self.opc_node_map[(lift_id_key, name)] = node
                if name == "xTrayInElevator":
                    # Register a write handler using the asyncua subscription mechanism
                    async def tray_write_handler(node, val):
                        logger.info(f"[OPC] External write to {lift_id_key} xTrayInElevator: {val}")
                        self.lift_state[lift_id_key]["xTrayInElevator"] = bool(val)
                        return val
                    node.data_set = tray_write_handler  # asyncua will call this on external writes

            elevator_eco_obj = await eco_to_plc_obj.add_object(self.namespace_idx, elevator_name)
            assign_obj_name = f"{elevator_name}EcoSystAssignment"
            eco_assign_obj = await elevator_eco_obj.add_object(self.namespace_idx, assign_obj_name)

            eco_assignment_specific_vars_map = {
                "Eco_iTaskType": (ua.VariantType.Int64, "iTaskType"),
                "Eco_iOrigination": (ua.VariantType.Int64, "iOrigination"),
                "Eco_iDestination": (ua.VariantType.Int64, "iDestination"),
            }
            for state_key, (ua_type_val, opc_name) in eco_assignment_specific_vars_map.items():
                node = await eco_assign_obj.add_variable(self.namespace_idx, opc_name, initial_lift_state[state_key], datatype=ua_type_val)
                await node.set_writable()
                self.opc_node_map[(lift_id_key, state_key)] = node

            eco_elevator_direct_vars_map = {
                "Eco_xAcknowledgeMovement": (ua.VariantType.Boolean, "xAcknowledgeMovement"),
                "Eco_iCancelAssignment": (ua.VariantType.Int64, "iCancelAssignment"), # Let op de 'e' in Assignent als dat de OPC naam is
                "xClearError": (ua.VariantType.Boolean, "xClearError")
            }
            for state_key, (ua_type_val, opc_name) in eco_elevator_direct_vars_map.items():
                node = await elevator_eco_obj.add_variable(self.namespace_idx, opc_name, initial_lift_state[state_key], datatype=ua_type_val) 
                await node.set_writable()
                self.opc_node_map[(lift_id_key, state_key)] = node
        
        logger.info("OPC UA Server Variables Initialized with Di_Call_Blocks/OPC_UA structure")
        
    async def _update_opc_value(self, lift_id_or_system_key, state_var_name, value):
        value_for_opc = value
        if isinstance(value, str) and len(value) > 200 and state_var_name in ["sSeq_Step_comment", "sStationStateDescription", "sShortAlarmDescription", "sAlarmSolution"]:
            value_for_opc = value[:200]

        node_key = (lift_id_or_system_key, state_var_name)
        node = self.opc_node_map.get(node_key)

        if node:
            try:
                current_opc_val = await node.read_value()
                if current_opc_val != value_for_opc:
                    await node.write_value(value_for_opc)
            except Exception as e:
                logger.error(f"Failed to write OPC value for {node_key}: {e}")

        if lift_id_or_system_key == "System":
            if state_var_name in self.system_state: self.system_state[state_var_name] = value
        elif lift_id_or_system_key in self.lift_state:
            if state_var_name == "iElevatorRowLocation":
                logger.debug(f"[{lift_id_or_system_key}] Skipping automatic update of internal iElevatorRowLocation, updated only OPC to {value}")
                pass
            elif state_var_name in self.lift_state[lift_id_or_system_key]:
                self.lift_state[lift_id_or_system_key][state_var_name] = value


    async def _read_opc_value(self, lift_id_or_system_key, state_var_name):
        node_key = (lift_id_or_system_key, state_var_name)
        node = self.opc_node_map.get(node_key)
        value = None
        if node:
            try:
                value = await node.read_value()
                is_input_var = state_var_name.startswith("Eco_") or \
                               (lift_id_or_system_key == "System" and state_var_name == "xWatchDog") or \
                               (state_var_name == "xClearError") 
                if is_input_var:
                    if lift_id_or_system_key == "System":
                        if state_var_name in self.system_state: self.system_state[state_var_name] = value
                    elif lift_id_or_system_key in self.lift_state:
                        if state_var_name in self.lift_state[lift_id_or_system_key]:
                            self.lift_state[lift_id_or_system_key][state_var_name] = value
                return value
            except Exception as e:
                logger.error(f"Failed to read OPC value for {node_key}: {e}")
        
        if lift_id_or_system_key == "System": return self.system_state.get(state_var_name)
        elif lift_id_or_system_key in self.lift_state: return self.lift_state[lift_id_or_system_key].get(state_var_name)
        return None
        
    async def _simulate_sub_movement(self, lift_id):
        state = self.lift_state[lift_id]
        now = time.time()
        movement_finished_this_tick = False        
        
        if state["_sub_engine_moving"]:
            rows_to_move = abs(state["_move_target_pos"] - state["iElevatorRowLocation"])
            if state["iElevatorRowLocation"] == state["_move_target_pos"]:
                duration = 0.0
            else:
                duration = max(0.1, rows_to_move * LIFT_MOVEMENT_DURATION_PER_ROW_S)
            
            time_elapsed = now - state["_move_start_time"]
            if time_elapsed >= duration:
                logger.info(f"[{lift_id}] Engine movement finished. Reached: {state['_move_target_pos']}")
                await self._update_elevator_position_complete(lift_id, state["_move_target_pos"])
                state["_sub_engine_moving"] = False
                movement_finished_this_tick = True
        
        elif state["_sub_fork_moving"]:
            if now - state["_fork_start_time"] >= FORK_MOVEMENT_DURATION_S:
                logger.info(f"[{lift_id}] Fork movement finished. Reached: {state['_fork_target_pos']}")
                await self._update_opc_value(lift_id, "iCurrentForkSide", state["_fork_target_pos"]) # OPC updated, internal state follows
                state["_sub_fork_moving"] = False
                movement_finished_this_tick = True
                
                if state["_fork_pickup_pending"]:
                    # Check: alleen pickup uitvoeren als lift exact op origin staat en niet beweegt
                    origin = state.get("ActiveElevatorAssignment_iOrigination")
                    if state["iElevatorRowLocation"] == origin and not state["_sub_engine_moving"]:
                        logger.info(f"[{lift_id}] Processing pending tray pickup after fork movement (positie klopt)")
                        state["_fork_pickup_pending"] = False
                        await self._update_tray_status_complete(lift_id, True)
                    else:
                        logger.warning(f"[{lift_id}] Pickup pending maar lift niet op origin ({state['iElevatorRowLocation']} != {origin}) of nog bewegend. Pickup uitgesteld.")
                        # Pickup blijft pending tot juiste positie
                
                if state["_fork_release_pending"]:
                    logger.info(f"[{lift_id}] Processing pending tray release after fork movement")
                    state["_fork_release_pending"] = False
                    await self._update_tray_status_complete(lift_id, False)
        
        elif state["_fork_pickup_pending"] and not state["_sub_fork_moving"]:
            # Check: alleen pickup uitvoeren als lift exact op origin staat en niet beweegt
            origin = state.get("ActiveElevatorAssignment_iOrigination")
            if state["iElevatorRowLocation"] == origin and not state["_sub_engine_moving"]:
                if now - state["_fork_pickup_start_time"] >= FORK_MOVEMENT_DURATION_S: # Assuming pickup takes same time as fork movement
                    logger.info(f"[{lift_id}] Standalone tray pickup completed (positie klopt)")
                    state["_fork_pickup_pending"] = False
                    await self._update_tray_status_complete(lift_id, True)
                    movement_finished_this_tick = True
            else:
                logger.warning(f"[{lift_id}] Pickup pending maar lift niet op origin ({state['iElevatorRowLocation']} != {origin}) of nog bewegend. Pickup uitgesteld.")
                # Pickup blijft pending tot juiste positie
        
        elif state["_fork_release_pending"] and not state["_sub_fork_moving"]:
            if now - state["_fork_release_start_time"] >= FORK_MOVEMENT_DURATION_S: # Assuming release takes same time
                logger.info(f"[{lift_id}] Standalone tray release completed")
                state["_fork_release_pending"] = False
                await self._update_tray_status_complete(lift_id, False)
                movement_finished_this_tick = True
        
        return state["_sub_engine_moving"] or state["_sub_fork_moving"] or state["_fork_pickup_pending"] or state["_fork_release_pending"]

    async def _update_elevator_position_complete(self, lift_id, new_position):
        logger.info(f"[{lift_id}] Elevator position update complete. Position: {new_position}")
        self.lift_state[lift_id]["iElevatorRowLocation"] = new_position
        await self._update_opc_value(lift_id, "iElevatorRowLocation", new_position)
                
    async def _update_tray_status_complete(self, lift_id, has_tray):
        logger.info(f"[{lift_id}] Tray status update complete. Has tray: {has_tray}")
        self.lift_state[lift_id]["xTrayInElevator"] = has_tray
        await self._update_opc_value(lift_id, "xTrayInElevator", has_tray)
    
    async def _start_tray_pickup(self, lift_id):
        if lift_id in self.lift_state:
            state = self.lift_state[lift_id]
            current_position = state.get("iElevatorRowLocation")
            target_position = state.get("ActiveElevatorAssignment_iOrigination")
            # Versterkte check: alleen pickup starten als lift exact op origin staat en niet beweegt
            if state.get("_sub_engine_moving") or current_position != target_position:
                logger.warning(f"[{lift_id}] Tray pickup requested but elevator is not at target position for pickup. Current: {current_position}, Target: {target_position}, Moving: {state.get('_sub_engine_moving')}")
                return
            logger.info(f"[{lift_id}] Starting delayed tray pickup process at position {current_position}")
            state["_fork_pickup_pending"] = True
            state["_fork_pickup_start_time"] = time.time()
    
    async def _start_tray_release(self, lift_id):
        """
        Start the tray release process with a delay to match visualization.
        The actual status update happens after fork movement is complete.
        """

        if lift_id in self.lift_state:
            state = self.lift_state[lift_id]
            current_position = state.get("iElevatorRowLocation")
            target_position = state.get("ActiveElevatorAssignment_iDestination")

            if state.get("_sub_engine_moving") or current_position != target_position:
                logger.warning(f"[{lift_id}] Tray release requested but elevator is not at target position for release. Current: {current_position}, Target: {target_position}, Moving: {state.get('_sub_engine_moving')}")
                return

            logger.info(f"[{lift_id}] Starting delayed tray release process at position {current_position}")
            state["_fork_release_pending"] = True
            state["_fork_release_start_time"] = time.time()

            
    def _calculate_movement_range(self, current_pos, *positions):
        all_positions = [current_pos] + list(positions)
        valid_positions = [pos for pos in all_positions if pos > 0]
        if not valid_positions: return (0, 0)
        return (min(valid_positions), max(valid_positions))    

    def _check_lift_ranges_overlap(self, my_range, other_range):
        my_min, my_max = my_range
        other_min, other_max = other_range
        if my_min == 0 and my_max == 0: return False
        if other_min == 0 and other_max == 0: return False
        
        my_physical_min = self.to_physical_pos(my_min)
        my_physical_max = self.to_physical_pos(my_max)
        other_physical_min = self.to_physical_pos(other_min)
        other_physical_max = self.to_physical_pos(other_max)
        
        overlap = not (my_physical_max < other_physical_min or my_physical_min > other_physical_max)
        
        if overlap: 
            logger.warning(f"COLLISION DETECTED: My path {my_range} (fysiek: {my_physical_min}-{my_physical_max}) overlaps other's {other_range} (fysiek: {other_physical_min}-{other_physical_max}).")
        return overlap
    
    async def _process_lift_logic(self, lift_id):
        state = self.lift_state[lift_id]
        other_lift_id = LIFT2_ID if lift_id == LIFT1_ID else LIFT1_ID

        # --- FORCE ERROR STATUS DESCRIPTION IF ERROR ACTIVE ---
        if state["iErrorCode"] == 888 or self.emg_stop_active:
            error_desc = state.get("sShortAlarmDescription") or "EMG STOP"
            await self._update_opc_value(lift_id, "sStationStateDescription", error_desc)
        elif state["iErrorCode"] == 0 and not self.emg_stop_active:
            if state["iCycle"] == 10:
                await self._update_opc_value(lift_id, "sStationStateDescription", "Ready for Job")

        if state["_sub_engine_moving"] or state["_sub_fork_moving"] or state["_fork_pickup_pending"] or state["_fork_release_pending"]:
            still_busy_with_sub_movement = await self._simulate_sub_movement(lift_id)
            if still_busy_with_sub_movement:
                return

        ecosystem_cancel_reason = await self._read_opc_value(lift_id, "Eco_iCancelAssignment")
        if ecosystem_cancel_reason > 0:
            logger.info(f"[{lift_id}] EcoSystem cancel request: {ecosystem_cancel_reason}. Cycle: {state['iCycle']}.")
            if state["_sub_engine_moving"] or state["_sub_fork_moving"]:
                state["_sub_engine_moving"] = False; state["_sub_fork_moving"] = False
                logger.info(f"[{lift_id}] Movement interrupted by EcoSystem cancel.")

            state["_fork_pickup_pending"] = False
            state["_fork_release_pending"] = False

            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", 0)
            state["_current_job_valid"] = False

            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iOrigination", 0)
            await self._update_opc_value(lift_id, "Eco_iDestination", 0)
            await self._update_opc_value(lift_id, "Eco_iCancelAssignment", 0)

            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)

            if state["iErrorCode"] != 0:
                await self._update_opc_value(lift_id, "iErrorCode", 0)
                await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
                await self._update_opc_value(lift_id, "sAlarmSolution", "")

            await self._update_opc_value(lift_id, "iCycle", 10)
            await self._update_opc_value(lift_id, "sSeq_Step_comment", "Job cancelled by EcoSystem. To Ready.")
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            return

        still_busy_with_sub_movement = await self._simulate_sub_movement(lift_id)
        if still_busy_with_sub_movement: return

        current_cycle = state["iCycle"]
        step_comment = f"Cycle {current_cycle}"
        next_cycle = current_cycle

        task_type_from_eco = await self._read_opc_value(lift_id, "Eco_iTaskType")
        origination_from_eco = await self._read_opc_value(lift_id, "Eco_iOrigination")
        destination_from_eco = await self._read_opc_value(lift_id, "Eco_iDestination")
        acknowledge_movement = await self._read_opc_value(lift_id, "Eco_xAcknowledgeMovement")

        ecosystem_watchdog_status = await self._read_opc_value('System', "xWatchDog")

        if ecosystem_watchdog_status is False:
            state["_watchdog_plc_state"] = False
        elif ecosystem_watchdog_status is True:
            await self._update_opc_value('System', "xWatchDog", False)
            state["_watchdog_plc_state"] = True
        else:
            logger.warning(f"[{lift_id}] EcoSystem Watchdog returned unexpected value: {ecosystem_watchdog_status}")

        clear_error_request = await self._read_opc_value(lift_id, "xClearError")
        if clear_error_request and state["iErrorCode"] != 0:
            logger.info(f"[{lift_id}] Received xClearError request. Clearing error {state['iErrorCode']}.")
            await self._update_opc_value(lift_id, "iErrorCode", 0)
            await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
            await self._update_opc_value(lift_id, "sAlarmSolution", "")
            await self._update_opc_value(lift_id, "xClearError", False)
            state["iErrorCode"] = 0
            if current_cycle >= 800:
                 next_cycle = 10
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            logger.info(f"[{lift_id}] Error cleared. Current cycle {current_cycle}, next cycle will be {next_cycle}")

        logger.debug(f"[{lift_id}] Cycle={current_cycle}, Job: Type={task_type_from_eco}, Origin={origination_from_eco}, Dest={destination_from_eco}, Ack={acknowledge_movement}, ErrorCode={state['iErrorCode']}")

        # --- RESETLOGICA: FORCEER TERUG NAAR 10 NA FOUTRESET ---
        if state["iErrorCode"] == 0 and not self.emg_stop_active and (
            current_cycle >= 800 or current_cycle == 888 or current_cycle == 650):
            logger.info(f"[{lift_id}] Errorcode is 0 en geen noodstop actief, forceer cycle naar 10 (Ready for Job) vanuit {current_cycle} (alleen na fout).")
            await self._update_opc_value(lift_id, "iCycle", 10)
            await self._update_opc_value(lift_id, "sStationStateDescription", "Ready for Job")
            return

        elif current_cycle == -10:
            step_comment = "Initializing PLC and Subsystems"
            next_cycle = 0
        elif current_cycle == 0:
            step_comment = "Idle - Waiting for Enable"
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            next_cycle = 10
        elif current_cycle == 10:
            step_comment = "Ready for EcoSystem job"
            if state["iErrorCode"] == 0:
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            next_cycle = 20

        elif current_cycle == 20:
            step_comment = "Wacht op opdracht ecosysteem"
            if state["iErrorCode"] == 0:
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            if task_type_from_eco > 0 and state["iErrorCode"] == 0:
                logger.info(f"[{lift_id}] Received new job in Cycle 20: Type={task_type_from_eco}, Origin={origination_from_eco}, Dest={destination_from_eco}")
                is_job_acceptable = True
                rejection_code = 0
                rejection_msg = ""
                my_movement_range_for_collision_check = (0,0)

                # --- BLOCK FullAssignment/PreparePickUp if tray is present ---
                if state["xTrayInElevator"] and task_type_from_eco in [FullAssignment, PreparePickUp]:
                    is_job_acceptable = False
                    rejection_code = CANCEL_PICKUP_WITH_TRAY
                    rejection_msg = "Tray already present in elevator; only BringAway allowed."
                else:
                    # Collision range per job type
                    if task_type_from_eco == FullAssignment:
                        if not (origination_from_eco > 0 or origination_from_eco == -2) or not (destination_from_eco > 0 or destination_from_eco == -2):
                            is_job_acceptable = False; rejection_code = CANCEL_INVALID_ZERO_POSITION; rejection_msg = "Invalid origin/destination for FullAssignment"
                        else:
                            my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination_from_eco, destination_from_eco)
                    elif task_type_from_eco == MoveToAssignment:
                        if not (origination_from_eco > 0 or origination_from_eco == -2):
                            is_job_acceptable = False; rejection_code = CANCEL_INVALID_ZERO_POSITION; rejection_msg = "Invalid origin for MoveTo"
                        else:
                            my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination_from_eco)
                    elif task_type_from_eco == PreparePickUp:
                        if not (origination_from_eco > 0 or origination_from_eco == -2):
                            is_job_acceptable = False; rejection_code = CANCEL_INVALID_ZERO_POSITION; rejection_msg = "Invalid origin for PreparePickUp"
                        else:
                            # Neem altijd het volledige pad: huidige positie, origin, destination (indien destination > 0)
                            if destination_from_eco > 0 or destination_from_eco == -2:
                                my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination_from_eco, destination_from_eco)
                            else:
                                my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination_from_eco)
                    elif task_type_from_eco == BringAway:
                        logger.info(f"[{lift_id}] BringAway job requested. xTrayInElevator={state['xTrayInElevator']}")
                        if not state["xTrayInElevator"]:
                            is_job_acceptable = False; rejection_code = CANCEL_INVALID_ASSIGNMENT; rejection_msg = "No tray in elevator for BringAway (xTrayInElevator is False)"
                        elif not (destination_from_eco > 0 or destination_from_eco == -2):
                            is_job_acceptable = False; rejection_code = CANCEL_INVALID_ZERO_POSITION; rejection_msg = "Invalid destination for BringAway"
                        else:
                            my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], destination_from_eco)
                    else:
                        is_job_acceptable = False; rejection_code = CANCEL_INVALID_ASSIGNMENT; rejection_msg = f"Unknown task type: {task_type_from_eco}"

                if is_job_acceptable:
                    other_state = self.lift_state[other_lift_id]
                    other_task = other_state["ActiveElevatorAssignment_iTaskType"]
                    other_origin = other_state["ActiveElevatorAssignment_iOrigination"]
                    other_dest = other_state["ActiveElevatorAssignment_iDestination"]
                    other_move_range = (0,0)
                    # Verbeterde collision detection: neem altijd het volledige pad van de andere lift
                    if other_state["_current_job_valid"] and other_task > 0:
                        other_current_pos = other_state["iElevatorRowLocation"]
                        if other_task == FullAssignment:
                            other_move_range = self._calculate_movement_range(other_current_pos, other_origin, other_dest)
                            logger.info(f"[CollisionCheck] {other_lift_id} active job: type={other_task}, from {other_current_pos} via {other_origin} to {other_dest}, range={other_move_range}")
                        elif other_task == MoveToAssignment:
                            other_move_range = self._calculate_movement_range(other_current_pos, other_origin)
                            logger.info(f"[CollisionCheck] {other_lift_id} active job: type={other_task}, from {other_current_pos} to {other_origin}, range={other_move_range}")
                        elif other_task == PreparePickUp:
                            # Neem altijd het volledige pad: huidige positie, origin, destination (indien destination > 0)
                            if other_dest > 0 or other_dest == -2:
                                other_move_range = self._calculate_movement_range(other_current_pos, other_origin, other_dest)
                                logger.info(f"[CollisionCheck] {other_lift_id} active job: type={other_task}, from {other_current_pos} via {other_origin} to {other_dest}, range={other_move_range}")
                            else:
                                other_move_range = self._calculate_movement_range(other_current_pos, other_origin)
                                logger.info(f"[CollisionCheck] {other_lift_id} active job: type={other_task}, from {other_current_pos} to {other_origin}, range={other_move_range}")
                        elif other_task == BringAway:
                            other_move_range = self._calculate_movement_range(other_current_pos, other_dest)
                            logger.info(f"[CollisionCheck] {other_lift_id} active job: type={other_task}, from {other_current_pos} to {other_dest}, range={other_move_range}")
                        else:
                            other_move_range = self._calculate_movement_range(other_current_pos)
                            logger.info(f"[CollisionCheck] {other_lift_id} active job: type={other_task}, only at {other_current_pos}, range={other_move_range}")
                    else:
                        other_move_range = self._calculate_movement_range(other_state["iElevatorRowLocation"])
                        logger.info(f"[CollisionCheck] {other_lift_id} heeft geen actieve job. Positie: {other_state['iElevatorRowLocation']}")

                    logger.info(f"[CollisionCheck] {lift_id} new job: type={task_type_from_eco}, range={my_movement_range_for_collision_check} vs {other_lift_id} range={other_move_range}")
                    if self._check_lift_ranges_overlap(my_movement_range_for_collision_check, other_move_range):
                        is_job_acceptable = False; rejection_code = CANCEL_LIFTS_CROSS; rejection_msg = "Potential collision with other lift"
                        logger.warning(f"[{lift_id}] Collision detected in Cycle 20. My range: {my_movement_range_for_collision_check}, Other's range: {other_move_range}")

                if is_job_acceptable:
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", task_type_from_eco)
                    if task_type_from_eco == FullAssignment or task_type_from_eco == PreparePickUp:
                        if not state["xTrayInElevator"]:
                            logger.info(f"[{lift_id}] Task {task_type_from_eco} starting. xTrayInElevator is already False, no action needed.")
                        else:
                            logger.info(f"[{lift_id}] Task {task_type_from_eco} starting, but tray is present. Job will be rejected by later logic if niet toegestaan.")
                    plc_active_origination = origination_from_eco 
                    plc_active_destination = destination_from_eco
                    if task_type_from_eco == BringAway: plc_active_origination = state["iElevatorRowLocation"] 
                    elif task_type_from_eco == MoveToAssignment: plc_active_destination = 0
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", plc_active_origination)
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", plc_active_destination)
                    state["_current_job_valid"] = True 
                    await self._update_opc_value(lift_id, "iCancelAssignment", 0)
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
                    await self._update_opc_value(lift_id, "sAlarmSolution", "")
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_NOTIFICATION) 
                    step_comment = f"TaskType {task_type_from_eco} received (O:{origination_from_eco}, D:{destination_from_eco}). Proceeding to validation."
                    next_cycle = 25 
                else:
                    step_comment = f"Job Rejected: {rejection_msg}"
                    logger.warning(f"[{lift_id}] Job rejected in Cycle 20. Reason Code: {rejection_code}, Message: {rejection_msg}")
                    await self._update_opc_value(lift_id, "iCancelAssignment", rejection_code)
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment)
                    await self._update_opc_value(lift_id, "sAlarmSolution", "Check job parameters. Clear/send new job from EcoSystem.")
                    await self._update_opc_value(lift_id, "iErrorCode", 888); state["iErrorCode"] = 888 
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                    await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                    state["_current_job_valid"] = False
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_WARNING)
                    next_cycle = 20
        elif current_cycle == 25:
            if not state["_current_job_valid"]:
                logger.error(f"[{lift_id}] Reached Cycle 25 without a valid current job. Returning to Ready.")
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_WARNING)
                await self._update_opc_value(lift_id, "iCancelAssignment", CANCEL_INVALID_ASSIGNMENT)
                next_cycle = 10
            else:
                task_type = state["ActiveElevatorAssignment_iTaskType"]
                step_comment = f"Cycle 25: Routing TaskType {task_type}"
                logger.info(f"[{lift_id}] Cycle 25: Routing TaskType {task_type}. Origin: {state['ActiveElevatorAssignment_iOrigination']}, Dest: {state['ActiveElevatorAssignment_iDestination']}")
                if task_type == FullAssignment: next_cycle = 90 
                elif task_type == MoveToAssignment: next_cycle = 290
                elif task_type == PreparePickUp: next_cycle = 490
                elif task_type == BringAway: next_cycle = 400 
                else:
                    logger.error(f"[{lift_id}] Invalid task type {task_type} in Cycle 25. Resetting.")
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                    await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                    state["_current_job_valid"] = False
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", "Internal Error: Invalid Task Route")
                    await self._update_opc_value(lift_id, "iCancelAssignment", CANCEL_INVALID_ASSIGNMENT)
                    next_cycle = 10
        elif current_cycle == 90:
            step_comment = f"FullAss: Signaling Eco for origin {state['ActiveElevatorAssignment_iOrigination']}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_1)
            await self._update_opc_value("System", "System_Handshake_iRowNr", state["ActiveElevatorAssignment_iOrigination"])
            next_cycle = 95
        elif current_cycle == 95:
            step_comment = f"FullAss: Waiting Eco ack for origin {state['ActiveElevatorAssignment_iOrigination']}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 100
        elif current_cycle == 100: 
            next_cycle = 102
        elif current_cycle == 102: 
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"FullAss: Moving to Origin {target_loc}"
            if state["iElevatorRowLocation"] == target_loc: 
                logger.info(f"[{lift_id}] Cycle 102: Reached origin {target_loc}. Transitioning to 150.")
                next_cycle = 150
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = target_loc; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 150:
            origin = state["ActiveElevatorAssignment_iOrigination"]
            target_fork_side = OpperatorSide if origin <= 50 else RobotSide
            step_comment = f"FullAss: Prep forks at {origin} for side {target_fork_side}"
            if state["iElevatorRowLocation"] != origin:
                state["_move_target_pos"] = origin; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
            elif state["iCurrentForkSide"] == target_fork_side: 
                next_cycle = 155
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = target_fork_side; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True          
        elif current_cycle == 155:
            origin = state["ActiveElevatorAssignment_iOrigination"]
            target_fork_side = OpperatorSide if origin <= 50 else RobotSide
            position_correct = state["iElevatorRowLocation"] == origin
            not_moving = not state["_sub_engine_moving"]
            forks_positioned = state["iCurrentForkSide"] == target_fork_side
            if position_correct and not_moving and forks_positioned:
                if not state["xTrayInElevator"] and not state["_fork_pickup_pending"]:
                    step_comment = f"FullAss: Pickup at {origin}"
                    logger.info(f"[{lift_id}] Cycle 155: All conditions met for pickup. Location: {state['iElevatorRowLocation']}, Expected Origin: {origin}, Fork Side: {state['iCurrentForkSide']}")
                    # Extra check: alleen pickup starten als lift exact op origin staat en niet beweegt
                    await self._start_tray_pickup(lift_id)
                else:
                    logger.info(f"[{lift_id}] Cycle 155: Tray already present of pickup pending, skipping pickup.")
                next_cycle = 160
            else:
                if not position_correct and not state["_sub_engine_moving"]:
                    logger.warning(f"[{lift_id}] Elevator not at pickup position for cycle 155. Current: {state['iElevatorRowLocation']}, Target: {origin}. Starting movement.")
                    state["_move_target_pos"] = origin; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
                step_comment = f"FullAss: Waiting for pickup conditions at {origin}. PosOK:{position_correct}, NotMoving:{not_moving}, ForkOK:{forks_positioned}"
                logger.debug(f"[{lift_id}] Cycle 155: Waiting. PosOK:{position_correct}, NotMoving:{not_moving}, ForkOK:{forks_positioned}")
                next_cycle = 155
        elif current_cycle == 160:
            step_comment = "FullAss: Forks to middle after pickup"
            if state["xTrayInElevator"] and state["iCurrentForkSide"] == MiddenLocation:  # Ensure tray is picked up and forks are middle
                next_cycle = 190
            elif not state["_sub_fork_moving"] and state["iCurrentForkSide"] != MiddenLocation:
                state["_fork_target_pos"] = MiddenLocation; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 190:
            step_comment = f"FullAss: Signaling Eco for dest {state['ActiveElevatorAssignment_iDestination']}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_2)
            await self._update_opc_value("System", "System_Handshake_iRowNr", state["ActiveElevatorAssignment_iDestination"])
            next_cycle = 195
        elif current_cycle == 195:
            step_comment = f"FullAss: Waiting Eco ack for dest {state['ActiveElevatorAssignment_iDestination']}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                if not state["xTrayInElevator"]:
                    logger.error(f"[{lift_id}] FullAssignment error: No tray after pickup phase before moving to destination!")                    
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", "Error: No tray for drop-off")
                    await self._update_opc_value(lift_id, "iErrorCode", 888)
                    state["iErrorCode"] = 888
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                    await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                    state["_current_job_valid"] = False
                    next_cycle = 10 # Or 800 for error state
                else:
                    logger.info(f"[{lift_id}] FullAssignment ack for dest received. Proceeding to move to destination (cycle 410).")
                    next_cycle = 410
        elif current_cycle == 290:
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveTo: Signaling Eco for target {target_loc}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_1)
            await self._update_opc_value("System", "System_Handshake_iRowNr", target_loc)
            next_cycle = 295
        elif current_cycle == 295:
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveTo: Waiting Eco ack for target {target_loc}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 300
        elif current_cycle == 300:
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveTo: Moving to target {target_loc}"
            if state["iElevatorRowLocation"] == target_loc: next_cycle = 310
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = target_loc; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 310:
            step_comment = f"MoveTo: Complete at {state['ActiveElevatorAssignment_iOrigination']}. To Ready."
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            state["_current_job_valid"] = False
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
            next_cycle = 10
        elif current_cycle == 400:
            step_comment = f"BringAway: Start to dest {state['ActiveElevatorAssignment_iDestination']}"
            if not state["xTrayInElevator"]:
                step_comment = "BringAway Error: No tray!"                
                await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment)
                await self._update_opc_value(lift_id, "iErrorCode", 888)
                state["iErrorCode"] = 888
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                state["_current_job_valid"] = False
                next_cycle = 10
            else:
                next_cycle = 410
        elif current_cycle == 410:
            dest_pos = state["ActiveElevatorAssignment_iDestination"]
            step_comment = f"BringAway: Moving to dest {dest_pos}"
            if state["iElevatorRowLocation"] == dest_pos: next_cycle = 420
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = dest_pos; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 420:
            dest_pos = state["ActiveElevatorAssignment_iDestination"]
            step_comment = f"BringAway: At dest {dest_pos}. Signaling Eco."
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_2)
            await self._update_opc_value("System", "System_Handshake_iRowNr", dest_pos)
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 430
        elif current_cycle == 430:
            dest_pos = state["ActiveElevatorAssignment_iDestination"]
            target_side = RobotSide if self.get_side(dest_pos) == "robot" else OpperatorSide
            step_comment = f"BringAway: Forks to side {target_side} at {dest_pos}"
            if state["iElevatorRowLocation"] != dest_pos:
                 state["_move_target_pos"] = dest_pos; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
            elif state["iCurrentForkSide"] == target_side: next_cycle = 435
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = target_side; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True        
        elif current_cycle == 435:
            if state["xTrayInElevator"] and not state["_fork_release_pending"]:
                await self._start_tray_release(lift_id)
                step_comment = "BringAway: Releasing tray"
            elif not state["xTrayInElevator"]:
                step_comment = "BringAway: Tray already released"
            else:
                step_comment = "BringAway: Waiting for tray release to complete"
            next_cycle = 440
        elif current_cycle == 440:
            step_comment = "BringAway: Forks to middle after placing"
            # Ensure elevator is still at destination
            if state["iElevatorRowLocation"] != state["ActiveElevatorAssignment_iDestination"]:
                # Should not happen, but stay in this cycle
                pass
            elif state["xTrayInElevator"] and not state["_fork_release_pending"]:
                # Tray still present, start release
                await self._start_tray_release(lift_id)
            elif not state["xTrayInElevator"] and state["iCurrentForkSide"] == MiddenLocation:
                # Tray released and forks in middle, advance
                next_cycle = 450
            elif not state["_sub_fork_moving"] and state["iCurrentForkSide"] != MiddenLocation:
                # Tray released, but forks not in middle, move forks
                state["_fork_target_pos"] = MiddenLocation
                state["_fork_start_time"] = time.time()
                state["_sub_fork_moving"] = True
            # else: stay in 440, waiting for fork release to complete or fork movement to middle to start/complete
        elif current_cycle == 450: 
            next_cycle = 460
        elif current_cycle == 460:
            step_comment = "BringAway: Complete. To Ready."
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            state["_current_job_valid"] = False
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
            next_cycle = 10
        elif current_cycle == 490:
            orig_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PrepPickUp: Signaling Eco for origin {orig_loc}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_1)
            await self._update_opc_value("System", "System_Handshake_iRowNr", orig_loc)
            next_cycle = 495
        elif current_cycle == 495:
            orig_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PrepPickUp: Waiting Eco ack for origin {orig_loc}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 500
        elif current_cycle == 500:
            step_comment = f"PrepPickUp: Start (O:{state['ActiveElevatorAssignment_iOrigination']})"
            if state["xTrayInElevator"]:
                step_comment = "PrepPickUp Error: Tray present!"
                await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment)
                await self._update_opc_value(lift_id, "iErrorCode", CANCEL_PICKUP_WITH_TRAY)
                state["iErrorCode"] = CANCEL_PICKUP_WITH_TRAY
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                state["_current_job_valid"] = False
                next_cycle = 10
            else:
                next_cycle = 505
        elif current_cycle == 505:
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PrepPickUp: Moving to Origin {target_loc}"
            if state["iElevatorRowLocation"] == target_loc: next_cycle = 510
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = target_loc; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 510:
            origin_pos = state["ActiveElevatorAssignment_iOrigination"]
            target_fork_side = RobotSide if self.get_side(origin_pos) == "robot" else OpperatorSide
            step_comment = f"PrepPickUp: Prep forks at {origin_pos} for side {target_fork_side}"
            if state["iElevatorRowLocation"] != origin_pos:
                 state["_move_target_pos"] = origin_pos; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
            elif state["iCurrentForkSide"] == target_fork_side: next_cycle = 515
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = target_fork_side; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 515:
            step_comment = "PrepPickUp: Forks to middle"
            if state["iCurrentForkSide"] == MiddenLocation: next_cycle = 520
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = MiddenLocation; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 520:
            step_comment = "PrepPickUp: Complete. To Ready."
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            state["_current_job_valid"] = False
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
            next_cycle = 10
        elif current_cycle == 800:
            step_comment = f"Error {state['iErrorCode']}. Waiting xClearError."
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
            # Stays in 800 until xClearError or reset button clears the error
          # Handle Emergency Stop state specifically
        if self.emg_stop_active:
            # If emg_stop is active, override next_cycle and comments
            step_comment = "EMERGENCY STOP ACTIVE"
            next_cycle = 888 # Force to a dedicated EMG error cycle if not already there
            await self._update_opc_value(lift_id, "iErrorCode", 888)
            await self._update_opc_value(lift_id, "sStationStateDescription", "EMG STOP")
            await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
            await self._update_opc_value(lift_id, "sAlarmSolution", "Noodstop knop is ingedrukt, laat noodstop knop los.")
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)


        await self._update_opc_value(lift_id, "sSeq_Step_comment", step_comment)
        if not state["_sub_engine_moving"] and not state["_sub_fork_moving"] and \
           not state["_fork_pickup_pending"] and not state["_fork_release_pending"] and \
           next_cycle != current_cycle:
            logger.info(f"[{lift_id}] Cycle transition: {current_cycle} -> {next_cycle}")
            await self._update_opc_value(lift_id, "iCycle", next_cycle)

    async def _periodic_sync_tray_from_opcua(self):
        """Periodically sync xTrayInElevator from OPC UA to internal state (for external writes, e.g. GUI)."""
        while self.running:
            for lift_id_key in LIFTS:
                node = self.opc_node_map.get((lift_id_key, "xTrayInElevator"))
                if node:
                    try:
                        opc_val = await node.read_value()
                        if self.lift_state[lift_id_key]["xTrayInElevator"] != bool(opc_val):
                            logger.info(f"[SYNC] Detected external change for {lift_id_key} xTrayInElevator: {opc_val}")
                            self.lift_state[lift_id_key]["xTrayInElevator"] = bool(opc_val)
                    except Exception as e:
                        logger.warning(f"[SYNC] Failed to read xTrayInElevator for {lift_id_key}: {e}")
            await asyncio.sleep(0.1)

    async def run(self):
        self.running = True
        try:
            await self._initialize_server()
        except Exception as e:
            logger.error(f"Failed to initialize server: {e}", exc_info=True)
            return

        # Start the periodic tray sync task
        asyncio.create_task(self._periodic_sync_tray_from_opcua())
        
        async with self.server:
            logger.info("Dual Lift PLC Simulator Server Started.")
            self.running = True
            while self.running:
                try:
                    self._check_physical_buttons()
                    
                    if not self.emg_stop_active:
                        await self._process_lift_logic(LIFT1_ID)
                        await self._process_lift_logic(LIFT2_ID)
                    # If emg_stop_active, the _check_physical_buttons will handle EMG state
                    # and _activate_emergency_stop will set error states.
                    # The lifts won't process normal logic.
                                            
                except Exception as e:
                    logger.exception(f"Error in main processing loop: {e}")
                await asyncio.sleep(SIMULATION_CYCLE_TIME_MS / 1000.0)

    async def stop(self):
        self.running = False
        logger.info("Dual Lift PLC Simulator Stopping...")
        if GPIO_AVAILABLE:
            try:
                GPIO.cleanup()
                logger.info("GPIO cleanup completed")
            except Exception as e:
                logger.error(f"Error during GPIO cleanup: {e}")
    
    def _setup_gpio(self):
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(EMG_STOP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(RESET_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            logger.info(f"GPIO initialized: EMG_STOP on pin {EMG_STOP_PIN}, RESET on pin {RESET_PIN}")
        except Exception as e:
            logger.error(f"Failed to initialize GPIO: {e}")
            global GPIO_AVAILABLE # Ensure this is modified if setup fails
            GPIO_AVAILABLE = False 
            logging.warning("GPIO setup failed. Physical buttons will be disabled.")

    
    def _check_physical_buttons(self):
        if not GPIO_AVAILABLE:
            return
            
        try:
            emg_button_state = GPIO.input(EMG_STOP_PIN) # LOW when pressed (PUD_UP)
            reset_button_state = GPIO.input(RESET_PIN)   # LOW when pressed (PUD_UP)

            if emg_button_state == GPIO.LOW:
                if not self.emg_stop_active:
                    logger.warning("EMERGENCY STOP BUTTON PRESSED!")
                    self.emg_stop_active = True
                    asyncio.create_task(self._activate_emergency_stop())
            else: # EMG button is HIGH (not pressed)
                if self.emg_stop_active:
                    # This part is crucial: if the button is released, emg_stop_active should allow reset.
                    # We are just making the system *resettable*.
                    logger.info("Emergency stop button released. System can now be reset.")
                    # self.emg_stop_active = False # This should be set false only if reset is also pressed or conditions allow reset
                    # The current logic in _handle_reset_button already checks self.emg_stop_active.
                    # It might be better to allow reset ONLY when EMG is physically high.
                    pass


            if reset_button_state == GPIO.LOW:
                # Check if EMG is physically released before allowing reset
                if GPIO.input(EMG_STOP_PIN) == GPIO.HIGH: # EMG must be released
                    if self.emg_stop_active: # If it was active due to a previous press
                        logger.info("Reset button pressed AND Emergency Stop is released. Clearing EMG state.")
                        self.emg_stop_active = False # Now allow full reset sequence
                        asyncio.create_task(self._handle_reset_button()) # This will clear EMG_STOP_ERROR_CODE
                    elif any(self.lift_state[lift_id]["iErrorCode"] != 0 for lift_id in LIFTS):
                        logger.info("Reset button pressed. Clearing other errors.")
                        asyncio.create_task(self._handle_reset_button()) # For other errors
                    else:
                        logger.info("Reset button pressed, but no active emergency stop or other errors to clear.")
                else:
                    logger.warning("Reset button pressed, but Emergency Stop button is still physically pressed. Release EMG first.")
                
                time.sleep(0.2) # Simple debounce
                
        except Exception as e:
            logger.error(f"Error checking physical buttons: {e}")
            # Potentially set GPIO_AVAILABLE to False if there's a runtime error with GPIO
            # global GPIO_AVAILABLE
            # GPIO_AVAILABLE = False
            # logging.error("Disabling GPIO due to runtime error.")

    
    async def _activate_emergency_stop(self):
        try:
            logger.info("Activating Emergency Stop for all lifts.")
            await self._update_opc_value("System", "iMainStatus", STATUS_ERROR)
            for lift_id in LIFTS:
                state = self.lift_state[lift_id]
                state["_sub_engine_moving"] = False
                state["_sub_fork_moving"] = False
                state["_fork_pickup_pending"] = False
                state["_fork_release_pending"] = False
                state["_current_job_valid"] = False # Invalidate current job
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0) # Clear active task from PLC perspective

                await self._update_opc_value(lift_id, "iErrorCode", 888)
                await self._update_opc_value(lift_id, "sStationStateDescription", "EMG STOP")
                await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
                await self._update_opc_value(lift_id, "sAlarmSolution", "Noodstop knop is ingedrukt, laat noodstop knop los en reset het systeem.")
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                await self._update_opc_value(lift_id, "iCycle", 888) # Go to error cycle

                # Clear any pending handshake from PLC side as well
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)

        except Exception as e:
            logger.error(f"Error activating emergency stop: {e}")
    
    async def _handle_reset_button(self):
        try:
            # This function is now called when reset is pressed AND EMG is physically released.
            # The self.emg_stop_active flag would have been set to False by _check_physical_buttons
            # if the EMG button was released and reset was pressed.
            
            if self.emg_stop_active: 
                # This case should ideally not be hit if _check_physical_buttons logic is correct
                logger.warning("Reset button pressed, but emg_stop_active is still true. EMG button might still be physically pressed.")
                return

            logger.info("Processing reset button - clearing errors on both lifts")
            
            all_lifts_reset = True
            for lift_id in LIFTS:
                state = self.lift_state[lift_id]
                if state["iErrorCode"] != 0: # Check if there is an error to clear
                    logger.info(f"Resetting error on {lift_id}. Current ErrorCode: {state['iErrorCode']}")
                    await self._update_opc_value(lift_id, "iErrorCode", 0)
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
                    await self._update_opc_value(lift_id, "sAlarmSolution", "")
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
                    
                    # Also clear EcoSystem side variables that might have caused the error or are stale
                    await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                    await self._update_opc_value(lift_id, "Eco_iOrigination", 0)
                    await self._update_opc_value(lift_id, "Eco_iDestination", 0)
                    await self._update_opc_value(lift_id, "Eco_iCancelAssignment", 0)
                    await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)


                    if state["iCycle"] >= 800 or state["iErrorCode"] == EMG_STOP_ERROR_CODE: # If in error cycle or was EMG
                        await self._update_opc_value(lift_id, "iCycle", 10) # Go to ready state
                    state["_current_job_valid"] = False # Ensure any previous job is invalidated
                # else:
                    # logger.info(f"No error to clear on {lift_id}")
            
            # Check if all lifts are now error-free before setting system status to OK
            system_ok = True
            for lift_id in LIFTS:
                if self.lift_state[lift_id]["iErrorCode"] != 0:
                    system_ok = False
                    break
            
            if system_ok:
                await self._update_opc_value("System", "iMainStatus", STATUS_OK)
                logger.info("System status set to OK after reset.")
            else:
                await self._update_opc_value("System", "iMainStatus", STATUS_ERROR) # Or appropriate status
                logger.warning("System status remains not OK as some lifts still have errors after reset attempt.")

            # Clear any global handshake that might be stuck
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)

        except Exception as e:
            logger.error(f"Error handling reset button: {e}")

async def main():
    logger.info("Starting PLC Simulator (Dual Lift)")
    
    plc_sim = PLCSimulator_DualLift()
    
    try:
        if sys.platform == 'win32':
            # On Windows, signal handling for graceful shutdown can be tricky with asyncio
            # Using a simple approach here
            async def shutdown_wrapper(sig):
                logger.info(f"Received signal {sig}, shutting down gracefully...")
                await plc_sim.stop()
                # Allow some time for async tasks to complete
                await asyncio.sleep(1) 
                # Cancel all remaining tasks
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                asyncio.get_event_loop().stop()

            import signal
            signal.signal(signal.SIGINT, lambda sig, frame: asyncio.create_task(shutdown_wrapper(sig)))
            signal.signal(signal.SIGBREAK, lambda sig, frame: asyncio.create_task(shutdown_wrapper(sig))) # For Windows Ctrl+Break
            signal.signal(signal.SIGTERM, lambda sig, frame: asyncio.create_task(shutdown_wrapper(sig)))


        else: # For Linux/Mac
            import signal
            def handle_shutdown_signal(sig, frame):
                logger.info(f"Received signal {sig}, initiating graceful shutdown...")
                asyncio.create_task(plc_sim.stop())
                # Further shutdown steps might be needed here depending on how plc_sim.run() exits

            signal.signal(signal.SIGINT, handle_shutdown_signal)
            signal.signal(signal.SIGTERM, handle_shutdown_signal)
            
    except ImportError: # Handles cases where 'signal' module might not be fully available (e.g. some embedded contexts)
        logger.warning("Signal handlers could not be set up. Use Ctrl+C, but GPIO cleanup might not run.")
    except Exception as e:
        logger.warning(f"Could not set up signal handlers: {e}")
    
    try:
        await plc_sim.run()
    except asyncio.CancelledError:
        logger.info("PLC Simulator main task was cancelled.")
    except KeyboardInterrupt: # Catch KeyboardInterrupt directly if not handled by signals
        logger.info("KeyboardInterrupt caught in main, initiating graceful shutdown...")
    finally:
        logger.info("PLC Simulator shutting down in main's finally block...")
        if plc_sim.running: # If stop wasn't called by a signal
            await plc_sim.stop()
        # Ensure the loop stops if it hasn't already
        if sys.platform == 'win32' and asyncio.get_event_loop().is_running():
             asyncio.get_event_loop().stop() # Necessary for Windows signal handling to exit cleanly

        logger.info("PLC Simulator shutdown complete.")
        
if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:  # This might catch it if signals didn't
        logger.info("Application terminated by KeyboardInterrupt in __main__.")
    except Exception as e:
        logger.exception(f"Unhandled exception in __main__: {e}")
    finally:
        # Ensure GPIO cleanup happens if RPi.GPIO was used and an error occurred before plc_sim.stop()
        if GPIO_AVAILABLE:
            try:
                GPIO.cleanup()
                logger.info("Final GPIO cleanup in __main__.")
            except Exception as e:
                logger.error(f"Error during final GPIO cleanup in __main__: {e}")
        logger.info("Exiting application from __main__.")