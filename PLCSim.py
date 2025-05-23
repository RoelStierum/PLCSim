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
if (os.path.exists(log_filename)):
    try:
        open(log_filename, 'w').close()
        # print(f"Cleared log file: {log_filename}") # Keep console clean
    except Exception as e:
        print(f"Warning: Could not clear log file {log_filename}: {e}")

log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=logging.INFO, # Changed to INFO for more details during dev
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

    def __init__(self, endpoint="opc.tcp://127.0.0.1:4860/gibas/plc/"):
        self.server = Server()
        self.endpoint = endpoint
        self.namespace_idx = None
        self.opc_node_map = {}
        self.running = False
        self._task_duration = 2.0 # General simulation duration for some actions
        self._pickup_offset = 2
        
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
            "_current_job_valid": False
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
                "Eco_iCancelAssignment": (ua.VariantType.Int64, "iCancelAssignment"),
                "xClearError": (ua.VariantType.Boolean, "xClearError")
            }
            for state_key, (ua_type_val, opc_name) in eco_elevator_direct_vars_map.items():
                node = await elevator_eco_obj.add_variable(self.namespace_idx, opc_name, initial_lift_state[state_key], datatype=ua_type_val) 
                await node.set_writable()
                self.opc_node_map[(lift_id_key, state_key)] = node
        
        logger.info("OPC UA Server Variables Initialized with Di_Call_Blocks/OPC_UA structure and new global handshake")

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
            if state_var_name in self.lift_state[lift_id_or_system_key]:
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
        
        # Fallback to internal state
        if lift_id_or_system_key == "System": return self.system_state.get(state_var_name)
        elif lift_id_or_system_key in self.lift_state:
            return self.lift_state[lift_id_or_system_key].get(state_var_name)
        return None

    async def _simulate_sub_movement(self, lift_id):
        state = self.lift_state[lift_id]
        now = time.time()
        movement_finished_this_tick = False

        if state["_sub_engine_moving"]:
            # Calculate dynamic duration based on rows
            rows_to_move = abs(state["_move_target_pos"] - state["iElevatorRowLocation"])
            # Ensure a minimum duration even for 0 rows if a move was intended (e.g. already at target but flag was set)
            # However, if rows_to_move is 0, it means we are at the target, so duration should be 0.
            # The check `state["iElevatorRowLocation"] == target_loc` before starting a move should prevent 0-row moves.
            # If somehow _sub_engine_moving is true but we are at target, finish immediately.
            if state["iElevatorRowLocation"] == state["_move_target_pos"]:
                 duration = 0.0
            else:
                 duration = max(0.1, rows_to_move * LIFT_MOVEMENT_DURATION_PER_ROW_S) # Min duration 0.1s

            if now - state["_move_start_time"] >= duration:
                logger.info(f"[{lift_id}] Engine movement finished. Reached: {state['_move_target_pos']}")
                await self._update_opc_value(lift_id, "iElevatorRowLocation", state["_move_target_pos"])
                state["_sub_engine_moving"] = False
                movement_finished_this_tick = True
                
        elif state["_sub_fork_moving"]:
            if now - state["_fork_start_time"] >= FORK_MOVEMENT_DURATION_S:
                logger.info(f"[{lift_id}] Fork movement finished. Reached: {state['_fork_target_pos']}")
                await self._update_opc_value(lift_id, "iCurrentForkSide", state["_fork_target_pos"])
                state["_sub_fork_moving"] = False
                movement_finished_this_tick = True
        
        return state["_sub_engine_moving"] or state["_sub_fork_moving"] # Returns true if still actively moving

    def _calculate_movement_range(self, current_pos, *positions):
        all_positions = [current_pos] + list(positions)
        valid_positions = [pos for pos in all_positions if pos > 0]
        if not valid_positions: return (0, 0)
        return (min(valid_positions), max(valid_positions))

    def _check_lift_ranges_overlap(self, my_range, other_range):
        my_min, my_max = my_range
        other_min, other_max = other_range
        if my_min == 0 and my_max == 0: return False # My lift is not planning a move
        if other_min == 0 and other_max == 0: return False # Other lift is not planning a move / not relevant
        overlap = not (my_max < other_min or my_min > other_max)
        if overlap: logger.warning(f"COLLISION DETECTED: My path {my_range} overlaps other's {other_range}.")
        return overlap
    
    async def _process_lift_logic(self, lift_id):
        state = self.lift_state[lift_id]
        other_lift_id = LIFT2_ID if lift_id == LIFT1_ID else LIFT1_ID

        ecosystem_cancel_reason = await self._read_opc_value(lift_id, "Eco_iCancelAssignment")
        if ecosystem_cancel_reason > 0:
            logger.info(f"[{lift_id}] EcoSystem cancel request: {ecosystem_cancel_reason}. Cycle: {state['iCycle']}.")
            if state["_sub_engine_moving"] or state["_sub_fork_moving"]:
                state["_sub_engine_moving"] = False; state["_sub_fork_moving"] = False
                logger.info(f"[{lift_id}] Movement interrupted by EcoSystem cancel.")
            
            # Clear PLC's active job
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", 0)
            state["_current_job_valid"] = False

            # Clear EcoSystem job inputs on OPC
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iOrigination", 0)
            await self._update_opc_value(lift_id, "Eco_iDestination", 0)
            await self._update_opc_value(lift_id, "Eco_iCancelAssignment", 0) # Ack cancel
            
            await self._update_opc_value(lift_id, "iCancelAssignment", CANCEL_BY_ECOSYSTEM) # PLC reason
            
            # Clear global handshake
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)

            if state["iErrorCode"] != 0: # Clear any local error
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
        step_comment = f"Cycle {current_cycle}"  # Default comment
        next_cycle = current_cycle
        
        # Read inputs for logic
        task_type_from_eco = await self._read_opc_value(lift_id, "Eco_iTaskType")
        origination_from_eco = await self._read_opc_value(lift_id, "Eco_iOrigination")
        destination_from_eco = await self._read_opc_value(lift_id, "Eco_iDestination")
        acknowledge_movement = await self._read_opc_value(lift_id, "Eco_xAcknowledgeMovement") # Per-lift ack

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


        logger.debug(f"[{lift_id}] Cycle={current_cycle}, Job: Type={task_type_from_eco}, Origin={origination_from_eco}, Dest={destination_from_eco}, Ack={acknowledge_movement}, ErrorCode={state['iErrorCode']}")
        
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
            if task_type_from_eco > 0 and state["iErrorCode"] == 0:
                logger.info(f"[{lift_id}] Received new job in Cycle 10: Type={task_type_from_eco}, Origin={origination_from_eco}, Dest={destination_from_eco}")
                
                is_job_acceptable = True
                rejection_code = 0
                rejection_msg = ""
                my_movement_range_for_collision_check = (0,0)
                
                # Basic parameter validation
                if task_type_from_eco == FullAssignment: # TaskType 1
                    if not (origination_from_eco > 0 and destination_from_eco > 0):
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid origin/destination for FullAssignment"
                    else:
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination_from_eco, destination_from_eco)
                elif task_type_from_eco == MoveToAssignment: # TaskType 2
                    # For MoveTo, the target is specified by iOrigination from EcoSystem
                    if not (origination_from_eco > 0): # VALIDATION: Check iOrigination (which is the target)
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid origin for MoveTo" # Message updated: origin is the target
                    else:
                        # Collision check uses iOrigination as the target
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination_from_eco)
                elif task_type_from_eco == PreparePickUp: # TaskType 3
                    if not (origination_from_eco > 0):
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid origin for PreparePickUp"
                    else:
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], origination_from_eco)
                elif task_type_from_eco == BringAway: # TaskType 4
                    if not state["xTrayInElevator"]:
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ASSIGNMENT 
                        rejection_msg = "No tray in elevator for BringAway"
                    elif not (destination_from_eco > 0):
                        is_job_acceptable = False
                        rejection_code = CANCEL_INVALID_ZERO_POSITION
                        rejection_msg = "Invalid destination for BringAway"
                    else:
                        my_movement_range_for_collision_check = self._calculate_movement_range(state["iElevatorRowLocation"], destination_from_eco)
                else: # Unknown task type
                    is_job_acceptable = False
                    rejection_code = CANCEL_INVALID_ASSIGNMENT
                    rejection_msg = f"Unknown task type: {task_type_from_eco}"
                
                # Collision Check (if basic parameters are acceptable)
                if is_job_acceptable:
                    other_state = self.lift_state[other_lift_id]
                    other_task = other_state["ActiveElevatorAssignment_iTaskType"] # Use internal active task
                    other_origin = other_state["ActiveElevatorAssignment_iOrigination"]
                    other_dest = other_state["ActiveElevatorAssignment_iDestination"]
                    other_move_range = (0,0)
                    if other_state["_current_job_valid"] and other_task > 0:
                        if other_task == FullAssignment: other_move_range = self._calculate_movement_range(other_state["iElevatorRowLocation"], other_origin, other_dest)
                        elif other_task == MoveToAssignment: other_move_range = self._calculate_movement_range(other_state["iElevatorRowLocation"], other_origin)
                        elif other_task == PreparePickUp: other_move_range = self._calculate_movement_range(other_state["iElevatorRowLocation"], other_origin)
                        elif other_task == BringAway: other_move_range = self._calculate_movement_range(other_state["iElevatorRowLocation"], other_dest)
                        else: other_move_range = self._calculate_movement_range(other_state["iElevatorRowLocation"])
                    else: other_move_range = self._calculate_movement_range(other_state["iElevatorRowLocation"])

                    collision_with_other_lift = self._check_lift_ranges_overlap(my_movement_range_for_collision_check, other_move_range)

                    if collision_with_other_lift:
                        is_job_acceptable = False
                        rejection_code = CANCEL_LIFTS_CROSS
                        rejection_msg = "Potential collision with other lift"
                        logger.warning(f"[{lift_id}] Collision detected in Cycle 10. My range: {my_movement_range_for_collision_check}, Other\\'s range: {other_move_range}")

                if is_job_acceptable:
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", task_type_from_eco)
                    
                    # Explicitly set/reset tray status for tasks that define it at the start
                    if task_type_from_eco == FullAssignment or task_type_from_eco == PreparePickUp:
                        # These tasks start by assuming no tray / will pick one up.
                        # Unconditionally ensure internal state and OPC output reflect this.
                        logger.info(f"[{lift_id}] Task {task_type_from_eco} starting. Current internal xTrayInElevator: {state['xTrayInElevator']}. Ensuring it is set to False.")
                        await self._update_opc_value(lift_id, "xTrayInElevator", False)
                        logger.info(f"[{lift_id}] After ensuring xTrayInElevator is False, internal state is now: {state['xTrayInElevator']}.")
                    elif task_type_from_eco == BringAway:
                        # BringAway requires a tray. If not present, it's an error (handled later in cycle 400).
                        # No change to xTrayInElevator here; its presence is a precondition.
                        pass

                    plc_active_origination = origination_from_eco 
                    plc_active_destination = destination_from_eco # Default, used by FullAssignment, BringAway
                                        
                    if task_type_from_eco == BringAway:
                        plc_active_origination = state["iElevatorRowLocation"] 
                        # plc_active_destination remains 'destination' from EcoSystem for BringAway
                    elif task_type_from_eco == MoveToAssignment:
                        # For MoveTo, EcoSystem\'s 'iOrigination' is the target.
                        # PLC stores this target in its 'ActiveElevatorAssignment_iOrigination'.
                        # PLC\'s 'ActiveElevatorAssignment_iDestination' is not used for target by PLC, set to 0.
                        plc_active_origination = origination_from_eco # This is the target for MoveTo
                        plc_active_destination = 0         # Destination not used as PLC target for MoveTo
                    
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", plc_active_origination)
                    await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", plc_active_destination)
                    
                    state["_current_job_valid"] = True 
                    
                    await self._update_opc_value(lift_id, "iCancelAssignment", 0) # Corrected path to PlcToEco.StationData.X.iCancelAssignment
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
                    await self._update_opc_value(lift_id, "sAlarmSolution", "")
                    await self._update_opc_value(lift_id, "iStationStatus", STATUS_NOTIFICATION) 

                    step_comment = f"TaskType {task_type_from_eco} received (O:{origination_from_eco}, D:{destination_from_eco}). Proceeding to validation."
                    # All accepted jobs go to cycle 25 for further validation (or direct execution start)
                    # For simplicity, let's assume cycle 25 is a brief validation/routing step.
                    next_cycle = 25 
                else: # Job rejected in Cycle 10
                    step_comment = f"Job Rejected: {rejection_msg}"
                    logger.warning(f"[{lift_id}] Job rejected in Cycle 10. Reason Code: {rejection_code}, Message: {rejection_msg}")
                    
                    await self._update_opc_value(lift_id, "iCancelAssignment", rejection_code) # Corrected path
                    await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment) # Use step_comment for the message) 
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
        
        # --- FullAssignment Handshake (Cycles 90, 95, 190, 195) ---
        elif current_cycle == 90: # FullAssignment: Signal Origin
            step_comment = f"FullAss: Signaling Eco for origin {state['ActiveElevatorAssignment_iOrigination']}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_1)
            await self._update_opc_value("System", "System_Handshake_iRowNr", state["ActiveElevatorAssignment_iOrigination"])
            next_cycle = 95
        elif current_cycle == 95: # FullAssignment: Wait Ack Origin
            step_comment = f"FullAss: Waiting Eco ack for origin {state['ActiveElevatorAssignment_iOrigination']}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 100
        elif current_cycle == 190: # FullAssignment: Signal Destination
            step_comment = f"FullAss: Signaling Eco for dest {state['ActiveElevatorAssignment_iDestination']}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_2)
            await self._update_opc_value("System", "System_Handshake_iRowNr", state["ActiveElevatorAssignment_iDestination"])
            next_cycle = 195
        elif current_cycle == 195: # FullAssignment: Wait Ack Destination
            step_comment = f"FullAss: Waiting Eco ack for dest {state['ActiveElevatorAssignment_iDestination']}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 400 # This was original next cycle for FullAssignment to move to destination
                                 # which is now part of BringAway logic. If FullAssignment is truly separate,
                                 # this should go to a dedicated "move to destination" cycle for FullAssignment.
                                 # For now, assuming it means start of BringAway part of FullAssignment.
                logger.info(f"[{lift_id}] FullAssignment ack for dest received. Next cycle should be move to dest. Currently routing to 400 (BringAway start).")
                # This routing might need refinement if FullAssignment has distinct move-to-dest logic
                # For now, let's keep it simple and assume cycle 400 is the correct transition.
                # The ActiveElevatorAssignment_iDestination is already set.
                # The ActiveElevatorAssignment_iOrigination for the "BringAway" part of FullAssignment
                # should effectively be the current location of the lift.
                # This is handled in cycle 10 when setting up ActiveJob for BringAway.
                # Here, for FullAssignment, ActiveElevatorAssignment_iOrigination was the *original* pickup.
                # This needs careful thought.
                # For now, let's assume the existing ActiveElevatorAssignment_iDestination is the target for the next move.
                # The BringAway sequence (400-460) will use ActiveElevatorAssignment_iDestination.
                # Let's ensure xTrayInElevator is true.
                if not state["xTrayInElevator"]: # Should be true after pickup part of FullAssignment
                    logger.error(f"[{lift_id}] FullAssignment error: No tray after pickup phase before moving to destination!")
                    # Error handling
                else:
                    next_cycle = 410 # Go directly to move to destination part of BringAway sequence

        # --- FullAssignment Execution (Cycles 100-160, then transitions to BringAway-like sequence) ---
        elif current_cycle == 100: next_cycle = 102
        elif current_cycle == 102: # Move to Origin
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"FullAss: Moving to Origin {target_loc}"
            if state["iElevatorRowLocation"] == target_loc: next_cycle = 150
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = target_loc; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 150: # Prepare Forks for Pickup
            origin = state["ActiveElevatorAssignment_iOrigination"]
            target_fork_side = OpperatorSide if origin <= 50 else RobotSide
            step_comment = f"FullAss: Prep forks at {origin} for side {target_fork_side}"
            if state["iElevatorRowLocation"] != origin: # Ensure at origin
                state["_move_target_pos"] = origin; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
            elif state["iCurrentForkSide"] == target_fork_side: next_cycle = 155
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = target_fork_side; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 155: # Pickup
            await self._update_opc_value(lift_id, "xTrayInElevator", True)
            next_cycle = 160
        elif current_cycle == 160: # Move Forks to Middle
            step_comment = "FullAss: Forks to middle after pickup"
            if state["iCurrentForkSide"] == MiddenLocation: next_cycle = 190 # Ready for dest handshake
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = MiddenLocation; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        
        # --- MoveToAssignment (Cycles 290, 295, 300, 310) ---
        elif current_cycle == 290: # Signal Target
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveTo: Signaling Eco for target {target_loc}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_1)
            await self._update_opc_value("System", "System_Handshake_iRowNr", target_loc)
            next_cycle = 295
        elif current_cycle == 295: # Wait Ack Target
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveTo: Waiting Eco ack for target {target_loc}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 300
        elif current_cycle == 300: # Move to Target
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"MoveTo: Moving to target {target_loc}"
            if state["iElevatorRowLocation"] == target_loc: next_cycle = 310
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = target_loc; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 310: # MoveTo Complete
            step_comment = f"MoveTo: Complete at {state['ActiveElevatorAssignment_iOrigination']}. To Ready."
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0) # Clear active job
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0) # Clear Eco request
            state["_current_job_valid"] = False
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE) # Ensure handshake cleared
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
            next_cycle = 10

        # --- BringAway (Cycles 400-460) ---
        # This sequence is also used for the "drop-off" part of FullAssignment after cycle 195
        elif current_cycle == 400: # Start BringAway (or drop-off part of FullAss)
            step_comment = f"BringAway: Start to dest {state['ActiveElevatorAssignment_iDestination']}"
            if not state["xTrayInElevator"]:
                step_comment = "BringAway Error: No tray!"
                # Error handling... (set error code, go to cycle 10 or 800)
                await self._update_opc_value(lift_id, "sShortAlarmDescription", step_comment)
                await self._update_opc_value(lift_id, "iErrorCode", CANCEL_INVALID_ASSIGNMENT)
                state["iErrorCode"] = CANCEL_INVALID_ASSIGNMENT
                await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
                state["_current_job_valid"] = False
                next_cycle = 10
            else:
                next_cycle = 410
        elif current_cycle == 410: # Move to Destination
            dest_pos = state["ActiveElevatorAssignment_iDestination"]
            step_comment = f"BringAway: Moving to dest {dest_pos}"
            if state["iElevatorRowLocation"] == dest_pos: next_cycle = 420
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = dest_pos; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 420: # Arrived at Dest, Signal Eco, Wait Ack
            dest_pos = state["ActiveElevatorAssignment_iDestination"]
            step_comment = f"BringAway: At dest {dest_pos}. Signaling Eco."
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_2)
            await self._update_opc_value("System", "System_Handshake_iRowNr", dest_pos)
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 430
        elif current_cycle == 430: # Move Forks to Side
            dest_pos = state["ActiveElevatorAssignment_iDestination"]
            target_side = RobotSide if self.get_side(dest_pos) == "robot" else OpperatorSide
            step_comment = f"BringAway: Forks to side {target_side} at {dest_pos}"
            if state["iElevatorRowLocation"] != dest_pos: # Ensure at dest
                 state["_move_target_pos"] = dest_pos; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
            elif state["iCurrentForkSide"] == target_side: next_cycle = 435
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = target_side; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 435: # Place Tray
            await self._update_opc_value(lift_id, "xTrayInElevator", False)
            next_cycle = 440
        elif current_cycle == 440: # Move Forks to Middle
            step_comment = "BringAway: Forks to middle after placing"
            if state["iElevatorRowLocation"] != state["ActiveElevatorAssignment_iDestination"]: # Ensure at dest
                 state["_move_target_pos"] = state["ActiveElevatorAssignment_iDestination"]; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
            elif state["iCurrentForkSide"] == MiddenLocation: next_cycle = 450
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = MiddenLocation; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 450: next_cycle = 460 # Fork at Middle
        elif current_cycle == 460: # BringAway Complete
            step_comment = "BringAway: Complete. To Ready."
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            state["_current_job_valid"] = False
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE) # Ensure handshake cleared
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
            next_cycle = 10

        # --- PreparePickUp (Cycles 490, 495, 500-520) ---
        elif current_cycle == 490: # Signal Origin
            orig_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PrepPickUp: Signaling Eco for origin {orig_loc}"
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_1)
            await self._update_opc_value("System", "System_Handshake_iRowNr", orig_loc)
            next_cycle = 495
        elif current_cycle == 495: # Wait Ack Origin
            orig_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PrepPickUp: Waiting Eco ack for origin {orig_loc}"
            if acknowledge_movement:
                await self._update_opc_value(lift_id, "Eco_xAcknowledgeMovement", False)
                await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE)
                await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
                next_cycle = 500
        elif current_cycle == 500: # Start PreparePickUp
            step_comment = f"PrepPickUp: Start (O:{state['ActiveElevatorAssignment_iOrigination']})"
            if state["xTrayInElevator"]:
                step_comment = "PrepPickUp Error: Tray present!"
                # Error handling...
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
        elif current_cycle == 505: # Move to Origin
            target_loc = state["ActiveElevatorAssignment_iOrigination"]
            step_comment = f"PrepPickUp: Moving to Origin {target_loc}"
            if state["iElevatorRowLocation"] == target_loc: next_cycle = 510
            elif not state["_sub_engine_moving"]:
                state["_move_target_pos"] = target_loc; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
        elif current_cycle == 510: # Prepare Forks at Origin
            origin_pos = state["ActiveElevatorAssignment_iOrigination"]
            target_fork_side = RobotSide if self.get_side(origin_pos) == "robot" else OpperatorSide
            step_comment = f"PrepPickUp: Prep forks at {origin_pos} for side {target_fork_side}"
            if state["iElevatorRowLocation"] != origin_pos: # Ensure at origin
                 state["_move_target_pos"] = origin_pos; state["_move_start_time"] = time.time(); state["_sub_engine_moving"] = True
            elif state["iCurrentForkSide"] == target_fork_side: next_cycle = 515
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = target_fork_side; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 515: # Move Forks to Middle
            step_comment = "PrepPickUp: Forks to middle"
            if state["iCurrentForkSide"] == MiddenLocation: next_cycle = 520
            elif not state["_sub_fork_moving"]:
                state["_fork_target_pos"] = MiddenLocation; state["_fork_start_time"] = time.time(); state["_sub_fork_moving"] = True
        elif current_cycle == 520: # PreparePickUp Complete
            step_comment = "PrepPickUp: Complete. To Ready."
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "Eco_iTaskType", 0)
            state["_current_job_valid"] = False
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_OK)
            await self._update_opc_value("System", "System_Handshake_iJobType", HANDSHAKE_JOB_TYPE_IDLE) # Ensure handshake cleared
            await self._update_opc_value("System", "System_Handshake_iRowNr", 0)
            next_cycle = 10
            
        elif current_cycle == 800: # General Error State
            step_comment = f"Error {state['iErrorCode']}. Waiting xClearError."
            await self._update_opc_value(lift_id, "iStationStatus", STATUS_ERROR)
            # Stays in 800 unless xClearError is processed (handled at top of function)

        await self._update_opc_value(lift_id, "sSeq_Step_comment", step_comment)
        if not state["_sub_engine_moving"] and not state["_sub_fork_moving"] and next_cycle != current_cycle:
            logger.info(f"[{lift_id}] Cycle transition: {current_cycle} -> {next_cycle}")
            await self._update_opc_value(lift_id, "iCycle", next_cycle)

    async def run(self):
        try:
            await self._initialize_server()
        except Exception as e:
            logger.error(f"Failed to initialize server: {e}", exc_info=True)
            return

        async with self.server:
            logger.info("Dual Lift PLC Simulator Server Started.")
            self.running = True
            while self.running:
                try:
                    await self._process_lift_logic(LIFT1_ID)
                    await self._process_lift_logic(LIFT2_ID)
                except Exception as e:
                    logger.exception(f"Error in main processing loop: {e}")
                await asyncio.sleep(SIMULATION_CYCLE_TIME_MS / 1000.0)

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
        logger.info("Application terminated by KeyboardInterrupt (main).")
    except Exception as e:
        logger.exception(f"Unhandled exception in __main__: {e}")
    finally:
        logger.info("Exiting application.")
