\
import asyncio
import logging
from asyncua import Client, ua
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

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

    async def get_node(self, node_path_str: str): # node_path_str e.g., "GVL_OPC/PlcToEco/Elevator1/iCycle"
        if not self.is_connected or not self.client:
            logger.warning("OPCUAClient: get_node called without client connection.")
            return None
        if self.plc_ns_idx is None:
            logger.warning("OPCUAClient: get_node called before PLC namespace index is known.")
            return None

        try:
            parts = node_path_str.split('/')
            if not parts:
                logger.error(f"OPCUAClient: Node path string is empty or invalid: '{node_path_str}'")
                return None

            current_node = self.client.get_objects_node()
            logger.debug(f"OPCUAClient: Starting browse for '{node_path_str}' from Objects node: {current_node}")

            for part_name in parts:
                qualified_part_name = f"{self.plc_ns_idx}:{part_name}"
                try:
                    # logger.debug(f"OPCUAClient: Attempting to get child: {qualified_part_name} under {current_node.nodeid}")
                    next_node = await current_node.get_child(qualified_part_name)
                    if next_node:
                        current_node = next_node
                        # logger.debug(f"OPCUAClient: Found part '{part_name}', current node: {current_node.nodeid}")
                    else:
                        logger.error(f"OPCUAClient: Part '{part_name}' not found under '{current_node.nodeid}' for path '{node_path_str}'")
                        return None
                except ua.UaStatusCodeError as e:
                    logger.error(f"OPCUAClient: OPC UA Error getting child '{part_name}' for path '{node_path_str}': {e} (Code: {e.code})")
                    return None
                except Exception as e_inner:
                    logger.error(f"OPCUAClient: Unexpected error getting child '{part_name}' for path '{node_path_str}': {e_inner}")
                    return None
            
            logger.debug(f"OPCUAClient: Successfully found node for path '{node_path_str}': {current_node.nodeid}")
            return current_node

        except ua.UaStatusCodeError as e:
            logger.error(f"OPCUAClient: OPC UA Error finding node for path '{node_path_str}': {e} (Code: {e.code})")
            return None
        except Exception as e:
            logger.exception(f"OPCUAClient: Unexpected Error in get_node for path '{node_path_str}': {e}")
            return None

    async def read_variable(self, node_identifier: str) -> Optional[Any]: # Renamed from read_value to match EcoSystemSim
        if not self.is_connected:
            logger.warning("OPCUAClient: Read value called while not connected.")
            return None
        try:
            node = await self.get_node(node_identifier)
            if not node:
                # get_node already logs the error if it fails to find the node
                logger.warning(f"OPCUAClient: Cannot read variable, node not found for identifier: {node_identifier}")
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

    async def write_value(self, node_identifier: str, value: Any, datatype: Optional[ua.VariantType] = None) -> bool:
        if not self.is_connected:
            logger.warning("OPCUAClient: Write value called while not connected.")
            return False
        try:
            node = await self.get_node(node_identifier)
            if not node:
                logger.warning(f"OPCUAClient: Cannot write value, node not found for identifier: {node_identifier}")
                return False

            ua_variant_to_write = None

            if datatype: 
                ua_variant_to_write = ua.Variant(value, datatype)
                # Minimal logging for watchdog
                if "xWatchDog" not in node_identifier and "WatchDog" not in node_identifier : 
                    logger.info(f"OPCUAClient: Using provided datatype {datatype.name} for {node_identifier} (value: {value}).")
            else:
                if isinstance(value, bool):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Boolean)
                elif isinstance(value, int): 
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Int64) # Default to Int64 for platform consistency
                elif isinstance(value, float):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Double) # Default to Double
                elif isinstance(value, str):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.String)
                else:
                    ua_variant_to_write = ua.Variant(value) 
                if "xWatchDog" not in node_identifier and "WatchDog" not in node_identifier :
                    logger.info(f"OPCUAClient: Inferred datatype {ua_variant_to_write.VariantType.name} for {node_identifier} (value: {value}).")
            
            if "xWatchDog" not in node_identifier and "WatchDog" not in node_identifier :
                logger.info(f"OPCUAClient: Attempting to write value: {value} (Final UA Variant: {ua_variant_to_write}) to {node_identifier}")
            
            initial_type_used_for_write_attempt = ua_variant_to_write.VariantType

            try:
                await node.write_value(ua_variant_to_write)
                # logger.debug(f"OPCUAClient: Successfully wrote {value} to {node_identifier} with type {initial_type_used_for_write_attempt.name}.")
                return True
            except ua.UaStatusCodeError as type_error:
                if "BadTypeMismatch" in str(type_error) and isinstance(value, int):
                    logger.warning(f"OPCUAClient: Type mismatch for {node_identifier} with type {initial_type_used_for_write_attempt.name}. Trying alternative integer types.")
                    # Broader range of integer types, PLCSim uses i64 for some, i16 for others.
                    # Order from most likely (based on common PLC types) to less common.
                    # Int64 was default, Int32, Int16 are common. Unsigned variants also possible.
                    potential_types = [
                        ua.VariantType.Int32, ua.VariantType.Int16, 
                        ua.VariantType.UInt64, ua.VariantType.UInt32, ua.VariantType.UInt16,
                        ua.VariantType.SByte, ua.VariantType.Byte 
                    ]
                    # Filter out the one already tried
                    types_to_try = [ptype for ptype in potential_types if ptype != initial_type_used_for_write_attempt]
                    if initial_type_used_for_write_attempt != ua.VariantType.Int64 and ua.VariantType.Int64 not in types_to_try:
                        types_to_try.insert(0, ua.VariantType.Int64) # Prioritize Int64 if not default

                    for alt_type in types_to_try:
                        try:
                            logger.info(f"OPCUAClient: Retrying write for {node_identifier} with type {alt_type.name}")
                            alt_ua_value = ua.Variant(value, alt_type)
                            await node.write_value(alt_ua_value)
                            logger.info(f"OPCUAClient: Successfully wrote {value} with alternative type {alt_type.name} to {node_identifier}")
                            return True
                        except ua.UaStatusCodeError as alt_type_error:
                            if "BadTypeMismatch" in str(alt_type_error):
                                logger.debug(f"OPCUAClient: Type {alt_type.name} also mismatched for {node_identifier}: {alt_type_error}")
                            else: 
                                logger.warning(f"OPCUAClient: Non-mismatch OPC UA error with alt type {alt_type.name} for {node_identifier}: {alt_type_error}")
                                break # Stop trying if it's not a type mismatch
                        except Exception as alt_error:
                            logger.warning(f"OPCUAClient: Other error trying alt type {alt_type.name} for {node_identifier}: {alt_error}")
                            break # Stop trying on other errors
                    
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

