import asyncio
import logging
from asyncua import Server, ua
import random
import time
import os # Import os module for path handling

# Zorg dat de logs map bestaat
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Clear log file at startup (mode='w' will truncate existing file)
log_filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'plcsim.log')
# Clear the log file first if it exists
if (os.path.exists(log_filename)):
    try:
        open(log_filename, 'w').close()  # Open and immediately close to clear the file
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
logger = logging.getLogger("PLCSim_DualLift_ST")

# Set all asyncua loggers to ERROR level to significantly reduce verbosity
logging.getLogger("asyncua").setLevel(logging.ERROR)

LIFT1_ID = 'Lift1'
LIFT2_ID = 'Lift2'
LIFTS = [LIFT1_ID, LIFT2_ID]

# Task Type Constants from PLC Code (assuming these values)
FullAssignment = 1
MoveToAssignment = 2
PreparePickUp = 3
BringAway = 4 # Not fully simulated yet

# Fork Side Constants
MiddenLocation = 0
RobotSide = 1
OpperatorSide = 2

class PLCSimulator_DualLift_ST:
    def __init__(self, endpoint="opc.tcp://127.0.0.1:4860/gibas/plc/"): # Changed to localhost
        self.server = Server()
        self.endpoint = endpoint
        self.namespace_idx = None
        self.nodes = {LIFT1_ID: {}, LIFT2_ID: {}, 'System': {}}
        self.running = False
        self._task_duration = 2.0 # Faster simulation
        self._pickup_offset = 2  # Adding the missing pickup offset attribute with a default value of 2

        # --- Default PLC State Variables (Aligning with PLC code) ---
        self.lift_state_template = {
            # --- Status ---
            "iCycle": -10, # Main state machine variable
            "iCycle_prev": -10, # Store previous cycle
            "iStatus": 0, # Simplified representation of overall status for EcoSystem (use iCycle mapping?)
            "iStationStatus": 0, # Simplified station status
            "iErrorCode": 0,
            "sShortAlarmDescription": "",
            "sAlarmMessage": "",
            "sAlarmSolution": "",
            "sSeq_Step_comment": "Initializing", # HMI comment
            "xWatchDog": False,

            # --- Job Assignment (Inputs from EcoSystem) ---
            # Simulating i_EcoSystemElevatorAssignment structure
            "Eco_iTaskType": 0,
            "Eco_iOrigination": 0,
            "Eco_iDestination": 0,

            # --- Active Job (Internal processing) ---
            "ActiveElevatorAssignment_iTaskType": 0,
            "ActiveElevatorAssignment_iOrigination": 0,
            "ActiveElevatorAssignment_iDestination": 0,

            # --- Handshake (PLC writes, EcoSystem reads/acks) ---
            # Simulating iq_EcoAssignmentAcknwl structure
            "EcoAck_iAssingmentType": 0, # PLC sets type (1=GetTray, 2=SetTray)
            "EcoAck_iRowNr": 0,          # PLC sets target row number
            "EcoAck_xAcknowldeFromEco": False, # EcoSystem sets TRUE to acknowledge

            # --- Lift State ---
            "iElevatorRowLocation": 0, # Actual position (simulated, updated after move)
            "xTrayInElevator": False,
            "iCurrentForkSide": MiddenLocation, # Actual fork position (Using constant MiddenLocation=0)

            # --- Internal Control / Sub-function triggers ---
            "iReqForkPos": MiddenLocation, # Request to fork sub-function (Using constant MiddenLocation=0)
            "iToEnginGoToLoc": 0,        # Request to engine sub-function
            "xLiftAddPickupOffset": False, # Request to engine sub-function

            # --- Interlock / Reach ---
            "q_iActiveReachLow": 0,      # Calculated reach for this lift's potential job
            "q_iActiveReachHigh": 0,     # Calculated reach for this lift's potential job
            "i_iReachOtherLiftLow": 0,   # Input: Other lift's current low reach
            "i_iReachOtherLiftHigh": 0,  # Input: Other lift's current high reach

            # --- Error/Cancel ---
            "iCancelAssignmentReson": 0, # Reason code if job rejected (PLC sets)
            "xClearError": False,        # Input: EcoSystem requests error clear
            "xErrorClearedAck": False,   # Output: PLC confirms error cleared

            # --- Simulation Internals (Not exposed via OPC UA unless needed for debugging) ---
            "_watchdog_plc_state": False,
            "_sub_fork_moving": False,    # Simulate sub-function busy state
            "_sub_engine_moving": False,  # Simulate sub-function busy state
            "_move_target_pos": 0,
            "_move_start_time": 0,
            "_fork_target_pos": MiddenLocation, # Using constant MiddenLocation=0
            "_fork_start_time": 0,
            "_current_job_valid": False, # Flag if job passed checks
            "_Internal_Job_Step": "IDLE" # Handles temporary string states for complex logic flow
        }

        self.lift_state = {
            LIFT1_ID: self.lift_state_template.copy(),
            LIFT2_ID: self.lift_state_template.copy()
        }
        # Verschillende startposities voor de liften
        # Lift1 bovenin (lage rij nummers)
        self.lift_state[LIFT1_ID]['iElevatorRowLocation'] = 5  # Bovenin het systeem
        # Lift2 onderin (hoge rij nummers)  
        self.lift_state[LIFT2_ID]['iElevatorRowLocation'] = 90 # Onderin het systeem
        self.lift_state[LIFT1_ID]['iCycle'] = 0
        self.lift_state[LIFT2_ID]['iCycle'] = 0


    async def _initialize_server(self):
        logger.info(f"Setting up dual-lift server on endpoint: {self.endpoint}")
        
        # --- First initialize server with minimal setup ---
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name("Gibas Dual Lift PLC Simulator (ST)")
        
        # --- Wait a short time for server initialization ---
        await asyncio.sleep(1.0)
        logger.info("Server initialized with basic setup, now adding namespaces and nodes...")
        
        # --- Register our own namespace ---
        uri = "http://gibas.com/plc/"
        self.namespace_idx = await self.server.register_namespace(uri)
        logger.info(f"Registered namespace with index: {self.namespace_idx}")
        
        # --- Wait before creating objects ---
        await asyncio.sleep(0.5)
        
        # --- Create objects structure ---
        objects = self.server.nodes.objects
        # Create LiftSystem under Objects node
        lift_system = await objects.add_object(self.namespace_idx, "LiftSystem")
        logger.info(f"Created LiftSystem object node")

        # --- Create Lift Sub-Objects and Variables ---
        for lift_id in LIFTS:
            # Set initial state to Ready (cycle 10) so GUI can send jobs immediately
            self.lift_state[lift_id]['iCycle'] = 10
            self.lift_state[lift_id]['iStatus'] = 10
            self.lift_state[lift_id]['sSeq_Step_comment'] = "Ready - Waiting for Job Assignment"
            
            # Add Lift object under LiftSystem node
            lift_obj = await lift_system.add_object(self.namespace_idx, lift_id)
            logger.info(f"Creating variables for {lift_id}")
            
            # Filter out internal simulation vars starting with '_'
            vars_to_create = {k: v for k, v in self.lift_state[lift_id].items() if not k.startswith('_')}

            for name, value in vars_to_create.items():
                # Determine the correct UA type based on Python type or name
                ua_type = ua.VariantType.String # Default
                if isinstance(value, bool): 
                    ua_type = ua.VariantType.Boolean
                elif isinstance(value, int): 
                    ua_type = ua.VariantType.Int16 # Assuming Int16 for PLC compatibility
                elif isinstance(value, float): 
                    ua_type = ua.VariantType.Float

                # Create the variable node under the lift object
                try:
                    node = await lift_obj.add_variable(self.namespace_idx, name, value, datatype=ua_type)
                    # Make the variable writable (optional, but often needed for inputs)
                    await node.set_writable()
                    # Store the node object for later access
                    self.nodes[lift_id][name] = node
                    # logger.debug(f"  Created variable {name} ({ua_type.name}) for {lift_id} with node ID {node.nodeid}")
                except Exception as e:
                    logger.error(f"Failed to create variable '{name}' for {lift_id}: {e}")

        logger.info("OPC UA Server Variables Initialized")


    # --- Helper functions _update_opc_value, _read_opc_value (adapt to new state dict keys) ---
    async def _update_opc_value(self, lift_id, name, value):
        """Update internal state and OPC UA node value for a specific lift."""
        state_updated = False
        if lift_id in self.lift_state and name in self.lift_state[lift_id]:
            # Prevent writing internal vars like _current_job_valid to OPC
            if not name.startswith('_'):
                # Speciaal geval: beperk de lengte van sSeq_Step_comment
                # Oude limiet was 40 karakters, nu verhoogd naar 100 voor betere leesbaarheid
                if name == "sSeq_Step_comment" and isinstance(value, str) and len(value) > 200:
                    # Tekst alleen inkorten bij extreme lengte 
                    value = value[:200]
                
                # Update internal state first
                self.lift_state[lift_id][name] = value
                state_updated = True
                
                # Update OPC UA node if it exists
                if lift_id in self.nodes and name in self.nodes[lift_id]:
                    node = self.nodes[lift_id][name]
                    try:
                        await node.write_value(value)
                        # logger.debug(f"Updated OPC: {lift_id}/{name} = {value}")
                    except Exception as e:
                        logger.error(f"Failed to write OPC value for {lift_id}/{name}: {e}")
                # else: logger.warning(f"Node not found in self.nodes for {lift_id}/{name}, cannot update OPC.") # More specific warning
            else:
                # Update internal state only for internal vars
                self.lift_state[lift_id][name] = value
                state_updated = True
                # logger.debug(f"Updated internal state only: {lift_id}/{name} = {value}")

        # elif lift_id == 'System' ... # No system vars needed currently

        if not state_updated:
             logger.warning(f"Attempted to update unknown state variable: {lift_id}/{name}")

    async def _read_opc_value(self, lift_id, name):
        """Read value from OPC UA node and update internal state cache."""
        # Only read variables expected to be inputs from EcoSystem
        is_input_from_eco = name.startswith("Eco_") or \
                            name == "EcoAck_xAcknowldeFromEco" or \
                            name == "xClearError"

        if not is_input_from_eco:
             # For non-input vars, just return the cached state
             return self.lift_state[lift_id].get(name)

        # For input vars, read from OPC and update cache
        if lift_id in self.nodes and name in self.nodes[lift_id]:
            node = self.nodes[lift_id][name]
            try:
                value = await node.read_value()
                # Update internal cache with the read value
                self.lift_state[lift_id][name] = value
                # logger.debug(f"Read OPC: {lift_id}/{name} = {value}")
                return value
            except Exception as e:
                logger.error(f"Failed to read OPC value for {lift_id}/{name}: {e}")
                # Return cached value on read error to avoid crashing logic
                return self.lift_state[lift_id].get(name)
        else:
            # logger.warning(f"Attempted to read unknown node: {lift_id}/{name}")
            return self.lift_state[lift_id].get(name) # Return cached value

    async def _toggle_watchdog(self, lift_id):
        state = self.lift_state[lift_id]
        state['_watchdog_plc_state'] = not state['_watchdog_plc_state']
        await self._update_opc_value(lift_id, "xWatchDog", state['_watchdog_plc_state'])

    async def _set_error(self, lift_id, code, short_desc, message, solution="Reset PLC and clear error.", cycle=888):
        """Put a specific lift into an error state."""
        # (Similar to before, ensure state variables match new names)
        logger.error(f"[{lift_id}] ERROR SET (Cycle {cycle}): Code={code}, Desc={short_desc}, Msg={message}")
        state = self.lift_state[lift_id]
        state['_current_job_valid'] = False # Invalidate job

        await self._update_opc_value(lift_id, "iErrorCode", code)
        await self._update_opc_value(lift_id, "sShortAlarmDescription", short_desc)
        await self._update_opc_value(lift_id, "sAlarmMessage", message)
        await self._update_opc_value(lift_id, "sAlarmSolution", solution)
        # await self._update_opc_value(lift_id, "iStatus", 888) # Status isn't directly used like this
        # await self._update_opc_value(lift_id, "iStationStatus", 888) # Status isn't directly used like this
        await self._update_opc_value(lift_id, "sSeq_Step_comment", f"ERROR: {short_desc}")
        await self._update_opc_value(lift_id, "iCycle", cycle) # Go to specific error cycle

    async def _clear_error(self, lift_id):
        # (Similar to before, ensure state variables match new names)
        logger.info(f"[{lift_id}] Clearing error state.")
        await self._update_opc_value(lift_id, "iErrorCode", 0)
        await self._update_opc_value(lift_id, "sShortAlarmDescription", "")
        await self._update_opc_value(lift_id, "sAlarmMessage", "")
        await self._update_opc_value(lift_id, "sAlarmSolution", "")
        await self._update_opc_value(lift_id, "xClearError", False) # Ack request
        await self._update_opc_value(lift_id, "xErrorClearedAck", True) # Signal cleared
        await self._update_opc_value(lift_id, "iCycle", -10) # Go to init state after clear
        await asyncio.sleep(0.5)
        await self._update_opc_value(lift_id, "xErrorClearedAck", False)

    def _calculate_reach(self, lift_id):
        """Calculate the potential reach for the current active job based on PLC logic."""
        state = self.lift_state[lift_id]
        # Use ActiveElevatorAssignment which is set after validation
        origin = state["ActiveElevatorAssignment_iOrigination"]
        destination = state["ActiveElevatorAssignment_iDestination"]
        current_pos = state["iElevatorRowLocation"]
        task_type = state["ActiveElevatorAssignment_iTaskType"]

        # If no valid job, reach is just current position
        if task_type == 0 or not state["_current_job_valid"]:
             state["q_iActiveReachLow"] = current_pos
             state["q_iActiveReachHigh"] = current_pos
             return
        
        # Implementation of GetReservedLocations from PLC code:
        # First calculate the high reach value
        if origin > destination:
            # Going down, origin is higher
            reserved_high = origin + self._pickup_offset
        elif origin < destination:
            # Going up, destination is higher
            reserved_high = destination + self._pickup_offset
        else:
            # Same position (shouldn't happen normally)
            reserved_high = current_pos + self._pickup_offset
        
        # Include current position if it's higher than calculated high reach
        if current_pos > reserved_high:
            reserved_high = current_pos
        
        # Now calculate the low reach value
        if origin < destination:
            # Going up, origin is lower
            reserved_low = origin
        elif origin > destination:
            # Going down, destination is lower
            reserved_low = destination
        else:
            # Same position (shouldn't happen normally)
            reserved_low = current_pos
            
        # Include current position if it's lower than calculated low reach
        if current_pos < reserved_low:
            reserved_low = current_pos
            
        # Set the reach values
        state["q_iActiveReachLow"] = reserved_low
        state["q_iActiveReachHigh"] = reserved_high
        
        logger.debug(f"[{lift_id}] Calculated Reach: Job({origin}->{destination}), Current({current_pos}) -> Reach({reserved_low}-{reserved_high})")

    async def _simulate_sub_movement(self, lift_id):
         """Simulate the progress of engine or fork movement."""
         state = self.lift_state[lift_id]
         now = time.time()

         # Simulate Engine Movement
         if state["_sub_engine_moving"]:
             if now - state["_move_start_time"] >= self._task_duration:
                 logger.info(f"[{lift_id}] Engine movement finished. Reached: {state['_move_target_pos']}")
                 await self._update_opc_value(lift_id, "iElevatorRowLocation", state["_move_target_pos"])
                 state["_sub_engine_moving"] = False
                 state["iToEnginGoToLoc"] = 0 # Clear trigger
                 
                 # Force cycle advancement for specific cycles
                 if state["iCycle"] == 155:
                     state["iCycle"] = 156  # Force advancement to next cycle
                 elif state["iCycle"] == 255:
                     state["iCycle"] = 256  # Force advancement after dropoff movement
                 # Signal completion implicitly by allowing state machine to proceed next cycle
             else:
                 # Still moving, stay in current cycle
                 # logger.debug(f"[{lift_id}] Engine still moving...")
                 pass

         # Simulate Fork Movement
         elif state["_sub_fork_moving"]:
              if now - state["_fork_start_time"] >= (self._task_duration / 4.0): # Faster fork move
                  logger.info(f"[{lift_id}] Fork movement finished. Reached: {state['_fork_target_pos']}")
                  await self._update_opc_value(lift_id, "iCurrentForkSide", state["_fork_target_pos"])
                  state["_sub_fork_moving"] = False
                  
                  # Force cycle advancement for multiple fork-related cycles
                  if state["iCycle"] == 150 or state["iCycle"] == 152:
                      state["iCycle"] = 153  # Skip to forks at side for pickup
                  elif state["iCycle"] == 160 or state["iCycle"] == 162:
                      state["iCycle"] = 163  # Skip to forks middle after pickup
                  elif state["iCycle"] == 250 or state["iCycle"] == 252:
                      state["iCycle"] = 253  # Skip to forks at side for place
                  elif state["iCycle"] == 260 or state["iCycle"] == 262:
                      state["iCycle"] = 263  # Skip to forks middle after dropoff
                      
                  # Signal completion implicitly by allowing state machine to proceed next cycle
              else:
                  # logger.debug(f"[{lift_id}] Forks still moving...")
                  pass

         # Return True if any sub-function is still busy
         return state["_sub_engine_moving"] or state["_sub_fork_moving"]

    # --- Main Logic mimicking the CASE Structure ---
    async def _process_lift_logic(self, lift_id):
        state = self.lift_state[lift_id]
        other_lift_id = LIFT2_ID if lift_id == LIFT1_ID else LIFT1_ID
        other_state = self.lift_state[other_lift_id]

        # Store previous cycle
        await self._update_opc_value(lift_id, "iCycle_prev", state["iCycle"])

        # --- Simulate Sub-Function execution ---
        # If a sub-function (move/fork) is active, wait for it to finish
        if await self._simulate_sub_movement(lift_id):
            return # Don't advance main state machine while sub-systems are busy

        # --- Read Inputs from EcoSystem ---
        # Only read when expecting input (e.g., cycle 20 for job, 100/201 for ack)
        # Read always for now, caching helps performance
        eco_task_type = await self._read_opc_value(lift_id, "Eco_iTaskType")
        eco_origin = await self._read_opc_value(lift_id, "Eco_iOrigination")
        eco_destination = await self._read_opc_value(lift_id, "Eco_iDestination")
        eco_ack = await self._read_opc_value(lift_id, "EcoAck_xAcknowldeFromEco")
        clear_error_req = await self._read_opc_value(lift_id, "xClearError")

        # --- Debug Log Job Request Variables ---
        logger.debug(f"[{lift_id}] Cycle={state['iCycle']}, JobVars: Type={eco_task_type}, Origin={eco_origin}, Dest={eco_destination}, Ack={eco_ack}")

        # --- Main CASE Logic ---
        current_cycle = state["iCycle"]
        next_cycle = current_cycle # Default to stay in state unless changed

        # Update HMI Comment based on current state before logic potentially changes it
        step_comment = f"Cycle {current_cycle}" # Default comment
        # Add specific comments later inside the CASE

        # --- Process based on current_cycle ---
        if current_cycle == 888: # Error State
            step_comment = state.get("sSeq_Step_comment", "Error State")
            if clear_error_req:
                await self._clear_error(lift_id)
                # State change handled by _clear_error

        elif current_cycle == 777: # Warning State
             step_comment = "Warning occurred, Initializing..."
             next_cycle = -10 # Auto init after warning

        elif current_cycle == -10: # Init
            step_comment = "Initializing PLC and Subsystems"
            # Simulate Init Steps (e.g., reset internal vars, check sub-systems)
            # In this simulation, just move to Idle
            state["iReqForkPos"] = MiddenLocation # Ensure forks start middle
            state["iToEnginGoToLoc"] = 0
            state["xLiftAddPickupOffset"] = False
            state["ActiveElevatorAssignment_iTaskType"] = 0 # Clear active job
            state["_current_job_valid"] = False
            state["iCancelAssignmentReson"] = 0
            await self._update_opc_value(lift_id, "iCancelAssignmentReson", 0) # Update OPC too
            # TODO: Simulate fork/engine init if needed
            next_cycle = 0

        elif current_cycle == 0: # Idle
            step_comment = "Idle - Waiting for Enable and Mode"
            # Reset things for new cycle
            state["ActiveElevatorAssignment_iTaskType"] = 0
            state["_current_job_valid"] = False
            # Check for Auto mode and Enable (assuming #xCMD_Enable and #xAuto_Mode are handled externally or always true for sim)
            # For simulation, let's assume always enabled in auto unless error
            next_cycle = 10

        elif current_cycle == 10: # Ready
            step_comment = "Ready - Waiting for Job Assignment"
            # Clear previous assignment display/ack state
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", 0)
            await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", 0)
            await self._update_opc_value(lift_id, "EcoAck_iAssingmentType", 0)
            await self._update_opc_value(lift_id, "EcoAck_iRowNr", 0)
            await self._update_opc_value(lift_id, "iCancelAssignmentReson", 0)
            state["_current_job_valid"] = False

            # Check for new job request from EcoSystem
            if eco_task_type > 0:
                # --- DIRECT DEBUG JOB RECEIVE ---
                # Print values that were directly read, not from state dictionary
                direct_eco_task_type = await self._direct_read_node_value(f"{lift_id}/Eco_iTaskType")
                direct_eco_origin = await self._direct_read_node_value(f"{lift_id}/Eco_iOrigination")
                direct_eco_destination = await self._direct_read_node_value(f"{lift_id}/Eco_iDestination")
                
                logger.info(f"******* JOB RECEIVED DEBUG *******")
                logger.info(f"[{lift_id}] Read method returned: Type={eco_task_type}, Origin={eco_origin}, Dest={eco_destination}")
                logger.info(f"[{lift_id}] Direct node read: Type={direct_eco_task_type}, Origin={direct_eco_origin}, Dest={direct_eco_destination}")
                logger.info(f"[{lift_id}] State dictionary: Type={state['Eco_iTaskType']}, Origin={state['Eco_iOrigination']}, Dest={state['Eco_iDestination']}")
                logger.info(f"********************************")
                
                # Copy request to internal processing variables
                state["ActiveElevatorAssignment_iTaskType"] = eco_task_type
                state["ActiveElevatorAssignment_iOrigination"] = eco_origin
                state["ActiveElevatorAssignment_iDestination"] = eco_destination
                
                # Update OPC values for active job
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", eco_task_type)
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iOrigination", eco_origin)
                await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iDestination", eco_destination)
                
                # Clear the request immediately after reading
                try:
                    # Write 0 to Eco_iTaskType via OPC UA
                    await self._write_value(f"{lift_id}/Eco_iTaskType", 0, ua.VariantType.Int16)
                    logger.info(f"[{lift_id}] Received Job Request: Type={eco_task_type}, Origin={eco_origin}, Dest={eco_destination}")
                except Exception as e:
                    logger.error(f"[{lift_id}] Failed to clear Eco_iTaskType: {e}")
                    
                next_cycle = 25 # Go to validation step
            # Add checks for SemiAutoMode (Cycle 15) or HomeMode (-40) if needed

        elif current_cycle == 25: # Validate Assignment
            step_comment = "Validating Job Assignment"
            task_type = state["ActiveElevatorAssignment_iTaskType"]
            origin = state["ActiveElevatorAssignment_iOrigination"]
            destination = state["ActiveElevatorAssignment_iDestination"]
            
            # Debug log for validation
            logger.info(f"[{lift_id}] Validating job: Type={task_type}, Origin={origin}, Dest={destination}")
            
            valid = True
            cancel_reason = 0
            error_msg = ""

            # --- Perform Reach Calculation for this lift ---
            state["_current_job_valid"] = True # Tentatively valid for reach calc
            self._calculate_reach(lift_id)
            my_reach_low = state["q_iActiveReachLow"]
            my_reach_high = state["q_iActiveReachHigh"]
            await self._update_opc_value(lift_id, "q_iActiveReachLow", my_reach_low)
            await self._update_opc_value(lift_id, "q_iActiveReachHigh", my_reach_high)


            # --- Get Other Lift's Reach (Read its calculated q_ values) ---
            # Note: Read directly from other lift's state cache for simulation accuracy
            other_reach_low = other_state["q_iActiveReachLow"]
            other_reach_high = other_state["q_iActiveReachHigh"]
            # Update the 'input' variables for this lift for OPC visibility
            await self._update_opc_value(lift_id, "i_iReachOtherLiftLow", other_reach_low)
            await self._update_opc_value(lift_id, "i_iReachOtherLiftHigh", other_reach_high)

            logger.debug(f"[{lift_id}] Validation Check: MyReach({my_reach_low}-{my_reach_high}), OtherReach({other_reach_low}-{other_reach_high})")

            # --- Perform Checks from PLC Code ---
            # Check 1: Lifts Cross? (doen de berekende reaches elkaar overlappen?)
            # Overlap exists if !(my_reach_high < other_reach_low OR my_reach_low > other_reach_high)
            # Dit is precies wat de PLC doet met de XOR logica
            overlap = not (my_reach_high < other_reach_low or my_reach_low > other_reach_high)
            
            # De PLC XOR logica in het GetReservedLocations functieblok:
            # IF #i_iReachOtherLiftHigh >= #q_iActiveReachLow XOR #i_iReachOtherLiftLow >= #q_iActiveReachHigh THEN
            cross_check_plc = (other_reach_high >= my_reach_low) ^ (other_reach_low >= my_reach_high) # XOR in Python is ^
            
            # Ook controleert de PLC op gelijkheid:
            # ELSIF #i_iReachOtherLiftHigh = #q_iActiveReachLow OR #i_iReachOtherLiftLow = #q_iActiveReachHigh THEN
            equal_boundaries = (other_reach_high == my_reach_low) or (other_reach_low == my_reach_high)
            
            if (cross_check_plc or equal_boundaries) and valid:
                # Check if the other lift is actually IDLE or at home position. If so, overlap might be acceptable.
                if other_state["iCycle"] != 0 and other_state["iCycle"] != 10: # Als andere lift actief is
                    logger.warning(f"[{lift_id}] Potential Collision Detected! MyReach({my_reach_low}-{my_reach_high}), OtherReach({other_reach_low}-{other_reach_high})")
                    valid = False
                    cancel_reason = 5 # Lifts cross each other
                    error_msg = f"Validation Error - Lifts cross each other (overlap in reach: {my_reach_low}-{my_reach_high} vs {other_reach_low}-{other_reach_high})"
                    logger.error(f"[{lift_id}] {error_msg}")

            # Check 2: Zero Origin/Destination for Full Move (Task 1)
            if valid and task_type == FullAssignment and (destination == 0 or origin == 0):
                valid = False
                cancel_reason = 4
                error_msg = "Validation Error - Dest/Origin cannot be 0 for Full Assignment"

            # Check 3: Zero Origin for Move/Prepare (Task 2 or 3)
            if valid and (task_type == MoveToAssignment or task_type == PreparePickUp) and origin == 0:
                 valid = False
                 cancel_reason = 4
                 error_msg = "Validation Error - Origin cannot be 0 for Move/Prepare Task"

            # Check 4: Pickup job while tray is already on forks
            if valid and (task_type == FullAssignment or task_type == PreparePickUp) and state["xTrayInElevator"]:
                valid = False
                cancel_reason = 1
                error_msg = "Validation Error - Pickup requested but tray already on forks"

            # Check 5: Destination is out of reach for this lift
            # We already calculated the reach, so now check if the destination is outside this lift's range
            # For this check we just use the presence of the other lift as the constraint
            lift1_pos = self.lift_state[LIFT1_ID]['iElevatorRowLocation']
            lift2_pos = self.lift_state[LIFT2_ID]['iElevatorRowLocation']
            
            # Now use the PLC's calculated reach to determine if the job is valid
            # A destination is unreachable if it equals the other lift's position
            if valid and task_type == FullAssignment:
                if lift_id == LIFT1_ID and destination == lift2_pos:
                    valid = False
                    cancel_reason = 5 # Lifts cross/reach problem
                    error_msg = f"Validation Error - Destination {destination} not reachable by {lift_id} (conflict with Lift2 at position {lift2_pos})"
                    logger.error(f"[{lift_id}] {error_msg}")
                elif lift_id == LIFT2_ID and destination == lift1_pos:
                    valid = False
                    cancel_reason = 5 # Lifts cross/reach problem
                    error_msg = f"Validation Error - Destination {destination} not reachable by {lift_id} (conflict with Lift1 at position {lift1_pos})"
                    logger.error(f"[{lift_id}] {error_msg}")

            # Check 6: Positive destination
            if valid and task_type == FullAssignment and destination <= 0:
                valid = False
                cancel_reason = 4
                error_msg = f"Validation Error - Invalid destination {destination} (must be > 0)"
                logger.error(f"[{lift_id}] {error_msg}")

            # Check for valid origin (also based on reach)
            if valid and (task_type == FullAssignment or task_type == PreparePickUp or task_type == MoveToAssignment):
                if lift_id == LIFT1_ID and origin == lift2_pos:
                    valid = False
                    cancel_reason = 5 # Lifts cross/reach problem
                    error_msg = f"Validation Error - Origin {origin} not reachable by {lift_id} (conflict with Lift2 at position {lift2_pos})"
                    logger.error(f"[{lift_id}] {error_msg}")
                elif lift_id == LIFT2_ID and origin == lift1_pos:
                    valid = False
                    cancel_reason = 5 # Lifts cross/reach problem
                    error_msg = f"Validation Error - Origin {origin} not reachable by {lift_id} (conflict with Lift1 at position {lift1_pos})"
                    logger.error(f"[{lift_id}] {error_msg}")

            # --- Outcome ---
            if valid:
                logger.info(f"[{lift_id}] Assignment Validated Successfully.")
                state["_current_job_valid"] = True
                next_cycle = 30 # Proceed to acceptance
            else:
                logger.error(f"[{lift_id}] {error_msg}")
                state["_current_job_valid"] = False
                state["ActiveElevatorAssignment_iTaskType"] = 0 # Clear invalid job
                await self._update_opc_value(lift_id, "iCancelAssignmentReson", cancel_reason)
                next_cycle = 650 # Go to rejection cycle

        elif current_cycle == 30: # Assignment Accepted
            step_comment = "Assignment Accepted - Preparing Execution"
            task_type = state["ActiveElevatorAssignment_iTaskType"]
            origin = state["ActiveElevatorAssignment_iOrigination"]
            destination = state["ActiveElevatorAssignment_iDestination"]
            current_pos = state["iElevatorRowLocation"]
            current_fork = state["iCurrentForkSide"]

            # Log detailed debug information
            logger.info(f"[{lift_id}] Processing Accepted Job: Type={task_type}, Origin={origin}, Dest={destination}")
            logger.info(f"[{lift_id}] Current Position: Pos={current_pos}, Fork={current_fork}")

            # Check if already at origin AND forks correct (for pickup tasks)
            if task_type == FullAssignment or task_type == PreparePickUp:
                 at_origin = (origin == current_pos)
                 # Determine required fork side for origin
                 req_fork_origin_side = RobotSide if origin < 100 else OpperatorSide # Placeholder logic for OverslagPunt
                 forks_correct = (current_fork == req_fork_origin_side)

                 if at_origin and forks_correct:
                     logger.info(f"[{lift_id}] Already at origin with correct forks, proceeding to pickup")
                     next_cycle = 155 # Skip to pickup offset
                 elif at_origin and not forks_correct:
                     logger.info(f"[{lift_id}] At origin but incorrect forks, proceeding to fork adjust")
                     next_cycle = 150 # Adjust forks first
                 else:
                     logger.info(f"[{lift_id}] Need to move to origin, proceeding to handshake")
                     next_cycle = 100 # Need handshake for GetTray
            elif task_type == MoveToAssignment:
                 logger.info(f"[{lift_id}] MoveTo task, proceeding to direct move")
                 next_cycle = 300 # Go to specific start cycle for MoveTo
            elif task_type == BringAway:
                 logger.info(f"[{lift_id}] BringAway task, proceeding to handshake")
                 next_cycle = 100 # Start with handshake
            else:
                 # Invalid task type here means error
                 logger.error(f"[{lift_id}] Invalid task type in cycle 30: {task_type}")
                 await self._set_error(lift_id, 650, "INVALID_TASK", f"Invalid Task Type {task_type} in cycle 30.")
                 cancel_reason = 6
                 await self._update_opc_value(lift_id, "iCancelAssignmentReson", cancel_reason)
                 next_cycle = 650

        elif current_cycle == 100: # Wait Handshake (GetTray)
            step_comment = "Waiting for EcoSystem Handshake (Get Tray)"
            await self._update_opc_value(lift_id, "EcoAck_iAssingmentType", 1) # 1 = GetTray
            await self._update_opc_value(lift_id, "EcoAck_iRowNr", state["ActiveElevatorAssignment_iOrigination"])

            # Direct check from node, bypassing potentially stale cache
            eco_ack_node = await self._direct_read_node_value(f"{lift_id}/EcoAck_xAcknowldeFromEco")
            if eco_ack_node:
                logger.info(f"[{lift_id}] Handshake Ack received for GetTray.")
                # Update internal state to match the acknowledgement
                state["EcoAck_xAcknowldeFromEco"] = True
                # Reset the acknowledgement flag
                await self._write_value(f"{lift_id}/EcoAck_xAcknowldeFromEco", False)
                # Reset handshake info after ack
                await self._update_opc_value(lift_id, "EcoAck_iAssingmentType", 0)
                await self._update_opc_value(lift_id, "EcoAck_iRowNr", 0)
                next_cycle = 101 # Start pickup sequence

        elif current_cycle == 101: # Check Forks before move
             step_comment = "Checking Fork Position before Move"
             # Assuming forks should be middle before starting a move sequence
             if state["iCurrentForkSide"] == MiddenLocation:
                  next_cycle = 102 # Proceed to move engine
             else:
                  logger.info(f"[{lift_id}] Forks not middle, moving to middle.")
                  state["iReqForkPos"] = MiddenLocation
                  state["_fork_target_pos"] = MiddenLocation
                  state["_fork_start_time"] = time.time()
                  state["_sub_fork_moving"] = True
                  # Stay in 101 until forks are middle

        elif current_cycle == 102: # Send Lift to Origin
             step_comment = "Moving Lift to Origin"
             target_loc = state["ActiveElevatorAssignment_iOrigination"]
             if state["iElevatorRowLocation"] == target_loc:
                 logger.info(f"[{lift_id}] Already at Origin {target_loc}.")
                 next_cycle = 105 # Skip move
             else:
                 state["iToEnginGoToLoc"] = target_loc
                 state["xLiftAddPickupOffset"] = False
                 state["_move_target_pos"] = target_loc
                 state["_move_start_time"] = time.time()
                 state["_sub_engine_moving"] = True
                 # Stay in 102 until move complete (handled by _simulate_sub_movement)
                 # Next cycle determined when _sub_engine_moving becomes false
                 next_cycle = 105 # Set the target cycle for after movement

        elif current_cycle == 105: # Arrived at Origin Row
             step_comment = "Arrived at Origin Row - Prepare Forks"
             # Engine move is finished at this point
             # next_cycle = 106 # PLC has intermediate steps, we go direct to fork prep
             next_cycle = 150

        elif current_cycle == 150: # Start Fork Process for Pickup
             step_comment = "Preparing Forks for Pickup"
             # next_cycle = 151 # PLC logic, skip to determination
             origin = state["ActiveElevatorAssignment_iOrigination"]
             # Simple logic: below 100 is RobotSide, 100+ is OperatorSide
             target_fork_side = RobotSide if origin < 100 else OpperatorSide
             state["iReqForkPos"] = target_fork_side
             state["_fork_target_pos"] = target_fork_side
             state["_fork_start_time"] = time.time()
             state["_sub_fork_moving"] = True
             logger.info(f"[{lift_id}] Moving forks to {target_fork_side} for pickup at {origin}")
             next_cycle = 152 # Wait for forks

        elif current_cycle == 152: # Waiting for Forks to reach side
             step_comment = "Waiting for Forks to reach Pickup Side"
             # Logic handled by _simulate_sub_movement
             # When done, it proceeds to 153
             next_cycle = 153

        elif current_cycle == 153: # Forks at side
             step_comment = "Forks Ready at Pickup Side"
             task_type = state["ActiveElevatorAssignment_iTaskType"]
             if task_type == PreparePickUp: # Task 3 ends here
                 next_cycle = 499
             elif task_type == FullAssignment: # Task 1 continues
                 next_cycle = 155

        elif current_cycle == 155: # Lift to Pickup Location (+ Offset)
             step_comment = "Moving Lift to Pickup (+ Offset)"
             target_loc = state["ActiveElevatorAssignment_iOrigination"]
             state["iToEnginGoToLoc"] = target_loc
             state["xLiftAddPickupOffset"] = True # Apply offset
             state["_move_target_pos"] = target_loc # Target is the row, offset handled internally by engine sim if needed
             state["_move_start_time"] = time.time()
             state["_sub_engine_moving"] = True
             # Simulate picking up the tray during this move
             await self._update_opc_value(lift_id, "xTrayInElevator", True)
             # Force transition to next state after engine movement
             next_cycle = 156 # Wait for move, but will transition in simulate_sub_movement

        elif current_cycle == 156: # Arrived at pickup (+offset)
             step_comment = "Arrived at Pickup Position"
             # Engine finished, tray picked up
             # next_cycle = 157 # Skip intermediate step
             next_cycle = 160 # Move forks back

        elif current_cycle == 160: # Move Forks to Middle after Pickup
             step_comment = "Moving Forks to Middle after Pickup"
             # next_cycle = 161 # Skip
             state["iReqForkPos"] = MiddenLocation
             state["_fork_target_pos"] = MiddenLocation
             state["_fork_start_time"] = time.time()
             state["_sub_fork_moving"] = True
             next_cycle = 162

        elif current_cycle == 162: # Waiting forks middle
             step_comment = "Waiting Forks to reach Middle"
             # Handled by simulation loop
             next_cycle = 163

        elif current_cycle == 163: # Forks middle
             step_comment = "Forks Middle - Pickup Sequence Done"
             # next_cycle = 165 # skip
             next_cycle = 199 # End of Pickup Phase

        elif current_cycle == 199: # End of GetTray phase
             step_comment = "Pickup Phase Complete"
             # In PLC this might check fork pos again, we assume it's ok
             # Ready for Place sequence if needed (FullAssignment)
             if state["ActiveElevatorAssignment_iTaskType"] == FullAssignment:
                 next_cycle = 201 # Wait for SetTray handshake
             else:
                 # Should not happen if logic is correct for PreparePickUp (ends at 499)
                 logger.warning(f"[{lift_id}] Unexpected arrival at 199 for task type {state['ActiveElevatorAssignment_iTaskType']}")
                 next_cycle = 10 # Go back to ready

        elif current_cycle == 201: # Wait Handshake (SetTray)
            step_comment = "Waiting for EcoSystem Handshake (Set Tray)"
            await self._update_opc_value(lift_id, "EcoAck_iAssingmentType", 2) # 2 = SetTray
            await self._update_opc_value(lift_id, "EcoAck_iRowNr", state["ActiveElevatorAssignment_iDestination"])

            if eco_ack:
                logger.info(f"[{lift_id}] Handshake Ack received for SetTray.")
                await self._update_opc_value(lift_id, "EcoAck_xAcknowldeFromEco", False) # Consume ack
                await self._update_opc_value(lift_id, "EcoAck_iAssingmentType", 0)
                await self._update_opc_value(lift_id, "EcoAck_iRowNr", 0)
                next_cycle = 202 # Start place sequence

        elif current_cycle == 202: # Send Lift to Destination (+ Offset)
             step_comment = "Moving Lift to Destination (+ Offset)"
             target_loc = state["ActiveElevatorAssignment_iDestination"]
             if state["iElevatorRowLocation"] == target_loc:
                  logger.info(f"[{lift_id}] Already at Destination {target_loc} (unexpected?).")
                  next_cycle = 205
             else:
                  state["iToEnginGoToLoc"] = target_loc
                  state["xLiftAddPickupOffset"] = True # Go slightly past? Check PLC SubSt_02_Engin logic for i_xTrayPickup=True on place
                  state["_move_target_pos"] = target_loc
                  state["_move_start_time"] = time.time()
                  state["_sub_engine_moving"] = True
                  next_cycle = 205 # Wait for move

        elif current_cycle == 205: # Arrived at Dest Row (+Offset)
             step_comment = "Arrived at Destination Row - Prepare Forks"
             # next_cycle = 206 # Skip
             next_cycle = 250

        elif current_cycle == 250: # Start Fork Process for Place
             step_comment = "Preparing Forks for Place"
             # next_cycle = 251 # Skip
             destination = state["ActiveElevatorAssignment_iDestination"]
             target_fork_side = RobotSide if destination < 100 else OpperatorSide
             state["iReqForkPos"] = target_fork_side
             state["_fork_target_pos"] = target_fork_side
             state["_fork_start_time"] = time.time()
             state["_sub_fork_moving"] = True
             logger.info(f"[{lift_id}] Moving forks to {target_fork_side} for place at {destination}")
             next_cycle = 252

        elif current_cycle == 252: # Waiting Forks Place Side
             step_comment = "Waiting for Forks to reach Place Side"
             # Handled by sim loop
             next_cycle = 253

        elif current_cycle == 253: # Forks at Place Side
             step_comment = "Forks Ready at Place Side"
             next_cycle = 255 # Move lift to dropoff height

        elif current_cycle == 255: # Lift to Dropoff Location (Exact)
             step_comment = "Moving Lift to Dropoff (Exact)"
             target_loc = state["ActiveElevatorAssignment_iDestination"]
             state["iToEnginGoToLoc"] = target_loc
             state["xLiftAddPickupOffset"] = False # Exact position
             state["_move_target_pos"] = target_loc
             state["_move_start_time"] = time.time()
             state["_sub_engine_moving"] = True
             # Simulate dropping the tray during move
             await self._update_opc_value(lift_id, "xTrayInElevator", False)
             next_cycle = 256

        elif current_cycle == 256: # Arrived at dropoff
             step_comment = "Arrived at Dropoff Position"
             # next_cycle = 257 # Skip
             next_cycle = 260 # Move forks back

        elif current_cycle == 260: # Move Forks Middle after Place
             step_comment = "Moving Forks to Middle after Place"
             # next_cycle = 261 # Skip
             state["iReqForkPos"] = MiddenLocation
             state["_fork_target_pos"] = MiddenLocation
             state["_fork_start_time"] = time.time()
             state["_sub_fork_moving"] = True
             next_cycle = 262

        elif current_cycle == 262: # Waiting forks middle
             step_comment = "Waiting Forks to reach Middle"
             # Handled by sim loop
             next_cycle = 263

        elif current_cycle == 263: # Forks Middle
             step_comment = "Forks Middle - Place Sequence Done"
             next_cycle = 299 # Job Finished

        elif current_cycle == 299: # Full Assignment Job Done
             step_comment = f"Task {FullAssignment} Done - Waiting EcoSystem Clear Task"
             # Wait for EcoSystem to clear the Eco_iTaskType (already done in cycle 10)
             # Or just go back to ready? PLC waits for Eco_iTaskType=0
             if eco_task_type == 0: # Check if request was cleared
                 next_cycle = 10 # Back to ready
             # If Eco_iTaskType still has old value, stay here.

        elif current_cycle == 300: # Start MoveTo Task
             step_comment = "Starting MoveTo Task"
             # Need to move directly to Destination
             # Reuse the move logic, but the target is destination
             # Need a state like REQ_MOVE_TO_DEST_DIRECT
             target_loc = state["ActiveElevatorAssignment_iDestination"]
             logger.info(f"[{lift_id}] Starting MoveTo: Current={state['iElevatorRowLocation']} -> Target={target_loc}")
             if state["iElevatorRowLocation"] == target_loc:
                 logger.info(f"[{lift_id}] Already at MoveTo Destination {target_loc}.")
                 next_cycle = 399 # Already there
             else:
                 # Need to request shaft access first
                 state["_current_job_valid"] = True # Mark job as valid for reach calc/move
                 self._calculate_reach(lift_id) # Calculate reach for the move
                 await self._update_opc_value(lift_id, "q_iActiveReachLow", state["q_iActiveReachLow"])
                 await self._update_opc_value(lift_id, "q_iActiveReachHigh", state["q_iActiveReachHigh"])
                 # Use the existing REQ_MOVE_TO_DEST logic, which handles shaft lock
                 next_cycle = "REQ_MOVE_TO_DEST" # Jump to shared logic state

        elif current_cycle == 399: # MoveTo Job Done
             step_comment = f"Task {MoveToAssignment} Done - Waiting EcoSystem Clear Task"
             if eco_task_type == 0:
                 next_cycle = 10

        elif current_cycle == 400: # Start PreparePickUp Task
             step_comment = "Starting PreparePickUp Task"
             # Logic is similar to FullAssignment start, go to handshake
             next_cycle = 100

        elif current_cycle == 499: # PreparePickUp Job Done
             step_comment = f"Task {PreparePickUp} Done - Waiting EcoSystem Clear Task"
             if eco_task_type == 0:
                 next_cycle = 10

        elif current_cycle == 650: # Assignment Rejected
             # Geef een meer beschrijvende foutmelding op basis van de cancel-reason code
             reason_code = state["iCancelAssignmentReson"]
             error_description = ""
             
             # Error code mapping naar meer specifieke omschrijving
             if reason_code == 1:
                 error_description = "Pickup assignment while tray is on forks"
             elif reason_code == 2:
                 error_description = "Destination out of reach"
             elif reason_code == 3:
                 error_description = "Origin out of reach"
             elif reason_code == 4:
                 error_description = "Destination/origin values not valid for this operation"
             elif reason_code == 5:
                 error_description = "Lifts cross each other or path blocked by other lift"
             elif reason_code == 6:
                 error_description = "Invalid assignment"
             else:
                 error_description = "Unknown error"
                 
             step_comment = f"Assignment Rejected (Reason: {reason_code} - {error_description}) - Waiting EcoSystem Clear Task"
             
             # PLC waits for Eco_iTaskType = 0
             if eco_task_type == 0:
                 next_cycle = 10 # Go back to ready state

        else: # Unknown cycle
            step_comment = f"Unknown Cycle {current_cycle} - Resetting"
            logger.warning(f"[{lift_id}] Entered unknown cycle {current_cycle}. Resetting.")
            next_cycle = -10


        # --- Update state for next iteration ---
        await self._update_opc_value(lift_id, "sSeq_Step_comment", step_comment)
        # Only change cycle if simulation is not busy with sub-movement AND next cycle is different
        if not state["_sub_engine_moving"] and not state["_sub_fork_moving"] and next_cycle != current_cycle:
            # Log cycle transition
            logger.info(f"[{lift_id}] Cycle transition: {current_cycle} -> {next_cycle}")
            # Handle jumping to the special state "REQ_MOVE_TO_DEST" if needed
            if isinstance(next_cycle, str):
                 await self._update_opc_value(lift_id, "_Internal_Job_Step", next_cycle) # Use internal step for shared logic
                 await self._update_opc_value(lift_id, "iCycle", 300) # Show a relevant cycle externally
            else:
                await self._update_opc_value(lift_id, "iCycle", next_cycle)
                # If we jumped out of the special state, clear it
                if isinstance(current_cycle, str):
                     await self._update_opc_value(lift_id, "_Internal_Job_Step", "IDLE") # No longer requesting move


    async def run(self):
        try:
            await self._initialize_server()
        except PermissionError as e:
             logger.error(f"#############################################################")
             logger.error(f"PERMISSION ERROR starting server: {e}")
             logger.error(f"This often means port {self.endpoint.split(':')[-1].split('/')[0]} is already in use.")
             logger.error(f"Check using 'netstat -ano | findstr \"{self.endpoint.split(':')[-1].split('/')[0]}\"' (Windows)")
             logger.error(f"Or try changing the port number in plcsim.py and ecosystemsim.py")
             logger.error(f"#############################################################")
             return # Stop execution
        except Exception as e:
             logger.error(f"Failed to initialize server: {e}")
             return

        async with self.server:
            logger.info("Dual Lift PLC Simulator Server Started (ST Logic).")
            self.running = True
            while self.running:
                try:
                    # Toggle watchdogs independently
                    await self._toggle_watchdog(LIFT1_ID)
                    await self._toggle_watchdog(LIFT2_ID)

                    # Process logic for both lifts
                    # BELANGRIJK: Gebruik _process_lift_logic (NIET _process_lift_logic_st)
                    await self._process_lift_logic(LIFT1_ID)
                    await self._process_lift_logic(LIFT2_ID)

                except Exception as e:
                     logger.exception(f"Error in main processing loop: {e}")
                     # Attempt to put both lifts in error? Or just log and continue? Log for now.
                     # Consider adding robust error handling here if needed

                await asyncio.sleep(0.2) # PLC cycle time

    async def stop(self):
        self.running = False
        logger.info("Dual Lift PLC Simulator Stopping...")

    # ============================================================
    #      NEW Process Logic Function based on ST Code
    # ============================================================
    async def _process_lift_logic_st(self, lift_id, current_logic_state):
          """ Processes one cycle using the state passed (iCycle or internal state string) """
          state = self.lift_state[lift_id]
          other_lift_id = LIFT2_ID if lift_id == LIFT1_ID else LIFT1_ID
          other_state = self.lift_state[other_lift_id]

          # Store previous actual iCycle
          await self._update_opc_value(lift_id, "iCycle_prev", state["iCycle"])

          # --- Simulate Sub-Function execution ---
          if await self._simulate_sub_movement(lift_id):
              return # Don't advance state machine while sub-systems are busy

          # --- Read Inputs ---
          eco_task_type = await self._read_opc_value(lift_id, "Eco_iTaskType")
          eco_origin = await self._read_opc_value(lift_id, "Eco_iOrigination")
          eco_destination = await self._read_opc_value(lift_id, "Eco_iDestination")
          eco_ack = await self._read_opc_value(lift_id, "EcoAck_xAcknowldeFromEco")
          clear_error_req = await self._read_opc_value(lift_id, "xClearError")

          next_cycle = current_logic_state # Default: stay in current logic state
          next_internal_step = "IDLE" # Default internal step change
          step_comment = f"Processing state {current_logic_state}" # Default comment

          # --- Main Logic (CASE Structure Simulation) ---
          try:
              # --- Handle String-based Internal Steps First ---
              if isinstance(current_logic_state, str):
                  step_comment = f"Internal Step: {current_logic_state}"
                  if current_logic_state == "REQ_MOVE_TO_ORIGIN" or current_logic_state == "REQ_MOVE_TO_DEST":
                      target = state['_current_job']["origin"] if current_logic_state == "REQ_MOVE_TO_ORIGIN" else state['_current_job']["destination"]
                      current_pos = state["iElevatorRowLocation"]

                      if current_pos == target:
                          logger.info(f"[{lift_id}] Already at target {target} for step {current_logic_state}.")
                          next_step_after_reach = "PICK_UP" if current_logic_state == "REQ_MOVE_TO_ORIGIN" else 399 # MoveTo ends here
                          if current_logic_state == "REQ_MOVE_TO_ORIGIN": next_internal_step = "PICK_UP"
                          else: next_cycle = 399 # Jump to numeric cycle
                          next_internal_step = "IDLE" # Clear internal step

                      else:
                           # Check shaft status for collision avoidance
                           # --- Get Other Lift's Reach ---
                           other_reach_low = other_state["q_iActiveReachLow"]
                           other_reach_high = other_state["q_iActiveReachHigh"]
                           # --- Use own calculated reach ---
                           my_reach_low = state["q_iActiveReachLow"]
                           my_reach_high = state["q_iActiveReachHigh"]

                           logger.debug(f"[{lift_id}] Shaft Check: MyReach({my_reach_low}-{my_reach_high}), OtherReach({other_reach_low}-{other_reach_high})")
                           overlap = not (my_reach_high < other_reach_low or my_reach_low > other_reach_high)

                           if overlap and other_state["iCycle"] != 0 and other_state["iCycle"] != 10:
                                logger.info(f"[{lift_id}] Waiting for shaft. Other lift active and reach overlaps.")
                                next_internal_step = current_logic_state # Stay in request state
                           else:
                                logger.info(f"[{lift_id}] Shaft free or no collision risk. Starting move to {target}.")
                                # Acquire simulated lock (no OPC var, just internal logic)
                                next_move_cycle = 102 if current_logic_state == "REQ_MOVE_TO_ORIGIN" else 202 # Go to appropriate move start cycle
                                if current_logic_state == "REQ_MOVE_TO_DEST" and state["ActiveElevatorAssignment_iTaskType"] == MoveToAssignment:
                                     next_move_cycle = 202 # MoveTo destination starts at 202 in this sim

                                next_cycle = next_move_cycle
                                next_internal_step = "IDLE" # Clear internal step


                  elif current_logic_state == "PICK_UP":
                       # This state is now handled numerically (e.g., 155)
                       logger.warning(f"[{lift_id}] Logic error: Ended up in internal step PICK_UP")
                       next_cycle = 155 # Try to recover
                       next_internal_step = "IDLE"
                  # Add other internal string steps if needed
                  else:
                      logger.error(f"[{lift_id}] Unknown internal step: {current_logic_state}")
                      next_cycle = -10 # Reset on error
                      next_internal_step = "IDLE"

              # --- Handle Numeric iCycle States ---
              elif isinstance(current_logic_state, int):
                   # Map numeric cycle logic here, mirroring the CASE statement
                   # (Copy relevant logic from the large block above)
                   # Example for cycle 10:
                   if current_logic_state == 10:
                       step_comment = "Ready - Waiting for Job Assignment"
                       await self._update_opc_value(lift_id, "ActiveElevatorAssignment_iTaskType", 0)
                       # ... (rest of cycle 10 logic) ...
                       if eco_task_type > 0:
                           # ... (copy job request) ...
                           logger.info(f"[{lift_id}] Received Job Request...")
                           next_cycle = 25
                   # ... Add elif blocks for all relevant iCycle values (0, 20, 25, 30, 100, 101, 102, ..., 650, etc.) ...
                   # Make sure to set 'next_cycle' or 'next_internal_step' appropriately
                   # Example for cycle 25 (Validation)
                   elif current_logic_state == 25:
                        step_comment = "Validating Job Assignment"
                        # ... (perform validation including reach check) ...
                        self._calculate_reach(lift_id) # Calculate own reach
                        # ... (get other lift reach) ...
                        # ... (perform XOR/overlap check) ...
                        valid = True # Assume valid initially
                        # ... (set valid=False and cancel_reason on error) ...
                        if valid:
                            state["_current_job_valid"] = True
                            next_cycle = 30
                        else:
                            state["_current_job_valid"] = False
                            # ... (set cancel reason) ...
                            next_cycle = 650

                   # Example cycle 102 (Move to Origin)
                   elif current_logic_state == 102:
                        step_comment = "Moving Lift to Origin"
                        target_loc = state["ActiveElevatorAssignment_iOrigination"]
                        if state["iElevatorRowLocation"] == target_loc:
                            next_cycle = 105
                        else:
                            state["iToEnginGoToLoc"] = target_loc
                            state["_move_target_pos"] = target_loc
                            state["_move_start_time"] = time.time()
                            state["_sub_engine_moving"] = True
                            # Stay in 102 while moving, _simulate_sub_movement handles exit
                            next_cycle = 102 # Explicitly stay until move done
                   # ... and so on for all cycles defined in the ST code ...

                   # Default for unknown numeric cycle
                   else:
                       # This check is now redundant if the main block covers all numeric cases
                       # logger.warning(f"[{lift_id}] Entered unknown cycle {current_logic_state}. Resetting.")
                       # next_cycle = -10
                       pass # Keep current cycle if not handled? Or reset?

          except Exception as e:
              logger.exception(f"[{lift_id}] CRITICAL ERROR during logic processing for state {current_logic_state}: {e}")
              # Attempt to set error state safely
              await self._set_error(lift_id, 999, "LOGIC_ERR", f"Exception in state {current_logic_state}", cycle=888)
              next_cycle = 888 # Ensure it goes to error state
              next_internal_step = "IDLE"


          # --- Update state for next iteration ---
          await self._update_opc_value(lift_id, "sSeq_Step_comment", step_comment)
          # Apply state changes if simulation is not busy and state changed
          if not state["_sub_engine_moving"] and not state["_sub_fork_moving"]:
              if next_internal_step != "IDLE":
                    current_opc_cycle = state["iCycle"] # What cycle to show externally?
                    if next_internal_step == "REQ_MOVE_TO_ORIGIN": current_opc_cycle=301 # Example mapping
                    elif next_internal_step == "REQ_MOVE_TO_DEST": current_opc_cycle=302 # Example mapping

                    await self._update_opc_value(lift_id, "_Internal_Job_Step", next_internal_step)
                    await self._update_opc_value(lift_id, "iCycle", current_opc_cycle) # Update external cycle too

              elif next_cycle != current_logic_state: # Check against the state we processed
                   await self._update_opc_value(lift_id, "iCycle", next_cycle)
                   # Clear internal step if we jumped based on numeric cycle
                   await self._update_opc_value(lift_id, "_Internal_Job_Step", "IDLE")

    async def _write_value(self, path, value, datatype=None):
        """Helper to write a value to an OPC UA node based on its path."""
        if not self.server or not self.namespace_idx:
            logger.error(f"Cannot write value, server not initialized: {path}")
            return False

        try:
            parts = path.split('/')
            lift_id = parts[0]
            name = parts[1]
            
            if lift_id in self.nodes and name in self.nodes[lift_id]:
                node = self.nodes[lift_id][name]
                if datatype:
                    ua_var = ua.Variant(value, datatype)
                    await node.write_value(ua_var)
                else:
                    await node.write_value(value)
                logger.debug(f"OPC Write: {path} = {value}")
                
                # Update internal state as well to keep in sync
                if lift_id in self.lift_state and name in self.lift_state[lift_id]:
                    self.lift_state[lift_id][name] = value
                    
                return True
            else:
                logger.error(f"Node path not found for writing: {path}")
                return False
        except Exception as e:
            logger.error(f"Error writing to OPC path {path}: {e}")
            return False

    async def _direct_read_node_value(self, path):
        """Direct read node value method for debugging, bypassing state cache."""
        try:
            parts = path.split('/')
            if len(parts) != 2:
                logger.error(f"Invalid node path format for direct read: {path}")
                return None
                
            lift_id = parts[0]
            name = parts[1]
            
            if lift_id in self.nodes and name in self.nodes[lift_id]:
                node = self.nodes[lift_id][name]
                try:
                    value = await node.read_value()
                    return value
                except Exception as e:
                    logger.error(f"Error reading value from node {path}: {e}")
                    # Return None on read error to avoid crashing logic
                    return None
            else:
                logger.error(f"Direct node read failed: Node not found for {path}")
                return None
        except Exception as e:
            logger.error(f"Error during direct node read for {path}: {e}")
            return None


# --- Main Execution Setup (remains the same) ---
if __name__ == "__main__":
    simulator = PLCSimulator_DualLift_ST()
    try:
        # asyncio.run(simulator.run(), debug=True) # Enable debug if needed
        asyncio.run(simulator.run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        logger.info("Dual Lift PLC Simulator Finished.")