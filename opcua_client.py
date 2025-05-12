\
import asyncio
import logging
from asyncua import Client, ua
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Definieer de interface variabelen volgens interface.txt
# Deze structuur representeert de hierarchie van variabelen zoals in interface.txt
class InterfaceVariables:
    # Van PLC naar Ecosysteem (PlcToEco)
    PLC_TO_ECO = {
        "iAmountOfSations",
        "iMainStatus",
        "StationData.iCycle",
        "StationData.sStationStateDescription",
        "StationData.sShortAlarmDescription",
        "StationData.sAlarmSolution",
        "StationData.iStationStatus",
        "StationData.Handshake.iRowNr",
        "StationData.Handshake.iJobType",
        "iCancelAssignment",
        "xWatchDog"  # watchdog is bi-directioneel
    }
    
    # Van Ecosysteem naar PLC (EcoToPlc)
    ECO_TO_PLC = {
        "xWatchDog",  # watchdog is bi-directioneel
        "Elevator1.ElevatorEcoSystAssignment.iTaskType",
        "Elevator1.ElevatorEcoSystAssignment.iOrigination",
        "Elevator1.ElevatorEcoSystAssignment.iDestination",
        "Elevator1.ElevatorEcoSystAssignment.xAcknowledgeMovement",
        "Elevator1.ElevatorEcoSystAssignment.iCancelAssignent",
        "Elevator2.ElevatorEcoSystAssignment.iTaskType",
        "Elevator2.ElevatorEcoSystAssignment.iOrigination",
        "Elevator2.ElevatorEcoSystAssignment.iDestination",
        "Elevator2.ElevatorEcoSystAssignment.xAcknowledgeMovement",
        "Elevator2.ElevatorEcoSystAssignment.iCancelAssignent"
    }
    
    # Alle toegestane interface variabelen
    ALL = PLC_TO_ECO.union(ECO_TO_PLC)

class VariableMapping:
    """Maps between interface variable names and the actual names used in the PLC implementation."""
    # We don't need to map anymore since we're using the correct interface names directly
    INTERFACE_TO_PLC = {
        # Leave empty - no mapping needed anymore
    }
    
    # Map from PLC implementation to interface specification
    PLC_TO_INTERFACE = {v: k for k, v in INTERFACE_TO_PLC.items()}
    
    @staticmethod
    def get_plc_name(interface_name):
        """Convert an interface variable name to its PLC implementation name"""
        # Return the original name since we're now using direct interface names
        return interface_name
        
    @staticmethod
    def get_interface_name(plc_name):
        """Convert a PLC implementation variable name to its interface specification name"""
        # Return the original name since we're now using direct interface names
        return plc_name

class OPCUAClient:
    def __init__(self, endpoint_url, ns_uri):
        self.endpoint_url = endpoint_url
        self.ns_uri = ns_uri
        self.client = Client(url=self.endpoint_url)
        self.plc_ns_idx = None
        self.is_connected = False

    async def connect(self):
        if self.is_connected:
            logger.info("OPCUAClient: Already connected.")
            return True
        try:
            logger.info(f"OPCUAClient: Attempting to connect to {self.endpoint_url}")
            await self.client.connect()
            self.plc_ns_idx = await self.client.get_namespace_index(self.ns_uri)
            self.is_connected = True
            logger.info(f"OPCUAClient: Connected to {self.endpoint_url}. Namespace Index: {self.plc_ns_idx}")
            return True
        except Exception as e:
            logger.error(f"OPCUAClient: Connection failed: {e}")
            self.is_connected = False
            return False

    async def disconnect(self):
        if self.client and self.is_connected:
            try:
                logger.info("OPCUAClient: Disconnecting from OPC UA server.")
                await self.client.disconnect()
            except Exception as e:
                logger.error(f"OPCUAClient: Error during disconnect: {e}")
            finally:
                self.is_connected = False
                logger.info("OPCUAClient: Disconnected.")
        else:
            logger.info("OPCUAClient: Client not connected or already disconnected.")
        self.is_connected = False # Ensure state is updated

    async def get_node(self, node_path_str): # node_path_str e.g., "Lift1/iCycle" or "StationData.iCycle"
        # Disable interface variable filtering
        allow_all_vars = True  # Set to True to allow all variables
        
        # Map interface variable name to PLC implementation name if needed
        if "ElevatorEcoSystAssignment" in node_path_str:
            # Try to map the interface name to the PLC implementation name
            plc_path = VariableMapping.get_plc_name(node_path_str)
            logger.info(f"OPCUAClient: Mapped interface path {node_path_str} to PLC path {plc_path}")
            node_path_str = plc_path  # Use the mapped name for searching

        # Original get_node code continues below
        # Extraheer de variabelenaam uit het pad (neem het laatste deel)
        parts = node_path_str.split('/')
        variable_name = parts[-1]
        
        # Skip interface variable checking if allow_all_vars is True
        if not allow_all_vars:
            # Controleer of het een interface variabele is (met een eenvoudige check op basis van naam)
            # Dit is een simpele check die we later kunnen verfijnen
            if '.' in variable_name:
                # Als het pad al een hiërarchische structuur bevat (bijv. StationData.iCycle)
                # neem dan alleen het laatste deel voor de eenvoudige check
                last_part = variable_name.split('.')[-1]
                is_interface_var = last_part in [name.split('.')[-1] for name in InterfaceVariables.ALL]
            else:
                is_interface_var = variable_name in [name.split('.')[-1] for name in InterfaceVariables.ALL]
                
            if not is_interface_var:
                logger.warning(f"OPCUAClient: Attempted to access non-interface variable: {variable_name}")
                return None
            
        if not self.is_connected or not self.client: # Added self.client check
            logger.warning("OPCUAClient: get_node called without client connection.")
            return None
        if self.plc_ns_idx is None:
            logger.warning("OPCUAClient: get_node called before PLC namespace index is known.")
            return None

        try:
            # Start vanaf de Objects node
            objects_node = self.client.get_objects_node()
            logger.debug(f"OPCUAClient: Starting browse for '{node_path_str}' from Objects node: {objects_node}")

            # Probeer eerst in de LiftSystem folder te zoeken
            try:
                parent_folder_name = "LiftSystem"
                qualified_parent_name = f"{self.plc_ns_idx}:{parent_folder_name}"
                logger.debug(f"OPCUAClient: Attempting to get child: {qualified_parent_name} under {objects_node}")

                lift_system_node = await objects_node.get_child(qualified_parent_name)
                if lift_system_node:
                    logger.debug(f"OPCUAClient: Found '{parent_folder_name}' node: {lift_system_node}")
                    
                    # Als we in de LiftSystem folder zijn, probeer dan verschillende locaties voor de variabele
                    # Opties afhankelijk van het type variabele dat we zoeken
                    search_nodes = [lift_system_node]
                    
                    # 1. Probeer het pad rechtstreeks onder LiftSystem
                    current_node_for_path = lift_system_node
                    for part_name in parts:
                        qualified_part_name = f"{self.plc_ns_idx}:{part_name}"
                        try:
                            next_node = await current_node_for_path.get_child(qualified_part_name)
                            if next_node:
                                current_node_for_path = next_node
                            else:
                                current_node_for_path = None
                                break
                        except:
                            current_node_for_path = None
                            break
                    
                    if current_node_for_path:
                        logger.debug(f"OPCUAClient: Successfully found node for path '{node_path_str}': {current_node_for_path.nodeid}")
                        return current_node_for_path
                    
                    # 2. Probeer alternatieve paden
                    # Eerst zoeken naar de PlcToEco node als het een PLC->Eco variabele is
                    try:
                        plc_to_eco_node = await lift_system_node.get_child(f"{self.plc_ns_idx}:PlcToEco")
                        if plc_to_eco_node:
                            search_nodes.append(plc_to_eco_node)
                    except:
                        pass
                        
                    # En ook zoeken naar de StationData node
                    try:
                        station_data_node = await lift_system_node.get_child(f"{self.plc_ns_idx}:StationData")
                        if station_data_node:
                            search_nodes.append(station_data_node)
                    except:
                        pass
                    
                    # Voor elke mogelijke parent, proberen de variabele te vinden
                    for search_node in search_nodes:
                        try:
                            var_node = await search_node.get_child(f"{self.plc_ns_idx}:{variable_name}")
                            if var_node:
                                logger.info(f"OPCUAClient: Found node {variable_name} under alternative path {search_node.nodeid}")
                                return var_node
                        except:
                            pass
            except Exception as e:
                logger.debug(f"OPCUAClient: Error searching in LiftSystem folder: {e}")
            
            # Als we hier komen, hebben we de variabele niet gevonden via de normale paden
            # Probeer nu recursief alle nodes te doorzoeken
            logger.debug(f"OPCUAClient: Performing recursive search for {variable_name}")
            
            async def find_variable_recursively(parent_node, var_name, depth=0, max_depth=3):
                if depth > max_depth:
                    return None
                
                try:
                    # Probeer eerst direct als child
                    try:
                        child = await parent_node.get_child(f"{self.plc_ns_idx}:{var_name}")
                        return child
                    except:
                        pass
                    
                    # Anders, doorzoek alle children
                    children = await parent_node.get_children()
                    for child in children:
                        # Check of dit node de gezochte node is
                        if var_name in str(child.nodeid) or var_name in str(await child.read_browse_name()):
                            return child
                        
                        # Recursief zoeken in deze child
                        result = await find_variable_recursively(child, var_name, depth + 1, max_depth)
                        if result:
                            return result
                except Exception as e:
                    logger.debug(f"Error during recursive search at depth {depth}: {e}")
                
                return None
            
            # Start de recursieve zoektocht vanaf de Objects node
            found_node = await find_variable_recursively(objects_node, variable_name)
            if found_node:
                logger.debug(f"OPCUAClient: Found node via recursive search: {found_node.nodeid}")
                return found_node
            
            # Als niets gevonden, geef een foutmelding
            logger.error(f"OPCUAClient: Node not found for variable {variable_name} after thorough search")
            return None

        except ua.UaStatusCodeError as e:
            logger.error(f"OPCUAClient: OPC UA Error finding node for path '{node_path_str}': {e} (Code: {e.code})")
            return None
        except Exception as e:
            logger.exception(f"OPCUAClient: Unexpected Error in get_node for path '{node_path_str}': {e}")
            return None

    async def read_value(self, node_identifier):
        # We'll allow all station data and internal variables for visualization
        allow_all_vars = True  # Set to True to disable variable filtering

        if not allow_all_vars:
            # Check if we are trying to read a StationData variable with a Lift prefix
            if '.' in node_identifier and ('StationData' in node_identifier):
                # Extract just the StationData part for validation
                parts = node_identifier.split('.')
                for i, part in enumerate(parts):
                    if part == 'StationData':
                        variable_name = '.'.join(parts[i:])
                        break
                else:
                    variable_name = node_identifier  # Fallback if StationData not found
            else:
                # Standard handling
                variable_name = node_identifier.split('/')[-1]
                
            # Check if variable is in the interface list
            var_in_interface = False
            for interface_var in InterfaceVariables.ALL:
                if variable_name in interface_var or interface_var in variable_name:
                    var_in_interface = True
                    break
                    
            if not var_in_interface:
                logger.warning(f"OPCUAClient: Attempted to read non-interface variable: {node_identifier}")
                return None
            
        if not self.is_connected:
            logger.warning("OPCUAClient: Read value called while not connected.")
            return None
        try:
            node = await self.get_node(node_identifier)
            if not node:
                # get_node already logs the error
                return None
            value = await node.read_value()
            logger.debug(f"OPCUAClient: Read value for {node_identifier}: {value}")
            return value
        except ua.UaStatusCodeError as e:
            logger.error(f"OPCUAClient: OPC UA Error reading value for {node_identifier}: {e} (Code: {e.code})")
            return None
        except Exception as e:
            logger.exception(f"OPCUAClient: Unexpected Error reading value for {node_identifier}: {e}")
            return None

    async def write_value(self, node_identifier, value, datatype=None):
        # Allow all variables by default for now
        allow_all_vars = True  # Set to True to disable variable filtering
        
        # Only check if not allowing all variables
        if not allow_all_vars:
            # Controleer eerst of het een Ecosysteem-naar-PLC variabele is
            variable_name = node_identifier.split('/')[-1]
            if variable_name not in InterfaceVariables.ECO_TO_PLC:
                logger.warning(f"OPCUAClient: Attempted to write to non-writable interface variable: {variable_name}")
                return False
            
        if not self.is_connected:
            logger.warning("OPCUAClient: Write value called while not connected.")
            return False
        try:
            # For ElevatorEcoSystAssignment paths, check if we need to map to a different name
            original_node_identifier = node_identifier
            if "ElevatorEcoSystAssignment" in node_identifier:
                # Try to map the interface name to the PLC implementation name
                plc_path = VariableMapping.get_plc_name(node_identifier)
                if plc_path != node_identifier:
                    logger.info(f"OPCUAClient: Write - Mapped interface path {node_identifier} to PLC path {plc_path}")
                    node_identifier = plc_path
            
            node = await self.get_node(node_identifier)
            if not node:
                # Try alternative separators if node not found
                alt_node_identifier = node_identifier.replace('/', '.')
                if alt_node_identifier != node_identifier:
                    logger.info(f"OPCUAClient: Trying alternative path separator: {alt_node_identifier}")
                    node = await self.get_node(alt_node_identifier)
                    
                # if still not found, try with original identifier just in case
                if not node and original_node_identifier != node_identifier:
                    logger.info(f"OPCUAClient: Trying with original identifier: {original_node_identifier}")
                    node = await self.get_node(original_node_identifier)
                    
                if not node:
                    # get_node already logs the error
                    return False

            ua_variant_to_write = None

            # Als een expliciet datatype is meegegeven
            if datatype: 
                ua_variant_to_write = ua.Variant(value, datatype)
                if "xWatchDog" not in node_identifier: # Condition added
                    logger.info(f"OPCUAClient: Using provided datatype {datatype.name} for {node_identifier} (value: {value}).")
            else:
                # Infer datatype if not provided
                if isinstance(value, bool):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Boolean)
                elif isinstance(value, int): 
                    # Standaard naar Int32 voor integers als geen specifiek type is gegeven.
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Int32) 
                elif isinstance(value, float):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Float)
                elif isinstance(value, str):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.String)
                else:
                    ua_variant_to_write = ua.Variant(value) # Let asyncua infer
                if "xWatchDog" not in node_identifier: # Condition added
                    logger.info(f"OPCUAClient: Inferred datatype {ua_variant_to_write.VariantType.name} for {node_identifier} (value: {value}).")

            if "xWatchDog" not in node_identifier: # Condition added
                logger.info(f"OPCUAClient: Attempting to write value: {value} (Final UA Variant: {ua_variant_to_write}) to {node_identifier}")
            
            initial_type_used_for_write_attempt = ua_variant_to_write.VariantType

            try:
                await node.write_value(ua_variant_to_write)
                logger.debug(f"OPCUAClient: Successfully wrote {value} to {node_identifier} with type {initial_type_used_for_write_attempt.name}.")
                return True
            except ua.UaStatusCodeError as type_error:
                # Fallback logic only if it's an integer.
                if "BadTypeMismatch" in str(type_error) and isinstance(value, int):
                    logger.info(f"OPCUAClient: Type mismatch for {node_identifier} with type {initial_type_used_for_write_attempt.name}. Trying alternative integer types.")
                    potential_types = [
                        ua.VariantType.Int16, ua.VariantType.Int32, ua.VariantType.Int64,
                        ua.VariantType.UInt16, ua.VariantType.UInt32, ua.VariantType.UInt64,
                        ua.VariantType.SByte, ua.VariantType.Byte
                    ]
                    types_to_try = [ptype for ptype in potential_types if ptype != initial_type_used_for_write_attempt]

                    for alt_type in types_to_try:
                        try:
                            logger.info(f"OPCUAClient: Retrying with type {alt_type.name} for {node_identifier}")
                            alt_ua_value = ua.Variant(value, alt_type)
                            await node.write_value(alt_ua_value)
                            logger.info(f"OPCUAClient: Successfully wrote {value} with alternative type {alt_type.name} to {node_identifier}")
                            return True
                        except ua.UaStatusCodeError as alt_type_error:
                            if "BadTypeMismatch" in str(alt_type_error):
                                logger.debug(f"OPCUAClient: Type {alt_type.name} also mismatched for {node_identifier}: {alt_type_error}")
                            else: 
                                logger.warning(f"OPCUAClient: Non-mismatch OPC UA error with alt type {alt_type.name} for {node_identifier}: {alt_type_error}")
                        except Exception as alt_error:
                            logger.debug(f"OPCUAClient: Other error trying alt type {alt_type.name} for {node_identifier}: {alt_error}")
                    
                    logger.error(f"OPCUAClient: All alternative integer types failed for {node_identifier}. Initial type was {initial_type_used_for_write_attempt.name}. Last error: {type_error}")
                    return False 
                else: 
                    logger.error(f"OPCUAClient: Unhandled OPC UA Error for {node_identifier} (Value: {value}, Type attempted: {initial_type_used_for_write_attempt.name}): {type_error}")
                    return False
        except ua.UaStatusCodeError as e:
            logger.error(f"OPCUAClient: OPC UA Error writing node {node_identifier} with value {value}: {e} (Code: {e.code})")
            return False
        except Exception as e:
            logger.exception(f"OPCUAClient: Unexpected Error writing node {node_identifier} with value {value}: {e}")
            return False

