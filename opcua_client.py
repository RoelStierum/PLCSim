\
import asyncio
import logging
from asyncua import Client, ua

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

    async def get_node(self, node_path_str): # node_path_str e.g., "Lift1/iCycle"
        if not self.is_connected or not self.client: # Added self.client check
            logger.warning("OPCUAClient: get_node called without client connection.")
            return None
        if self.plc_ns_idx is None:
            logger.warning("OPCUAClient: get_node called before PLC namespace index is known.")
            return None

        try:
            objects_node = self.client.get_objects_node()
            logger.debug(f"OPCUAClient: Starting browse for '{node_path_str}' from Objects node: {objects_node}")

            parent_folder_name = "LiftSystem"
            qualified_parent_name = f"{self.plc_ns_idx}:{parent_folder_name}"
            logger.debug(f"OPCUAClient: Attempting to get child: {qualified_parent_name} under {objects_node}")

            lift_system_node = await objects_node.get_child(qualified_parent_name)
            if not lift_system_node:
                logger.error(f"OPCUAClient: '{parent_folder_name}' node not found under Objects.")
                children = await objects_node.get_children_descriptions()
                logger.debug(f"OPCUAClient: Children of Objects node: {[child.BrowseName for child in children]}")
                return None
            logger.debug(f"OPCUAClient: Found '{parent_folder_name}' node: {lift_system_node}")

            current_node_for_path = lift_system_node
            parts = node_path_str.split('/')
            for i, part_name in enumerate(parts):
                qualified_part_name = f"{self.plc_ns_idx}:{part_name}"
                logger.debug(f"OPCUAClient: Attempting to get child: {qualified_part_name} under {current_node_for_path.nodeid}")
                try:
                    next_node = await current_node_for_path.get_child(qualified_part_name)
                    if not next_node:
                        logger.warning(f"OPCUAClient: Part '{part_name}' not found in path '{node_path_str}' under {current_node_for_path.nodeid}. Searched for {qualified_part_name}")
                        children_desc = await current_node_for_path.get_children_descriptions()
                        logger.debug(f"OPCUAClient: Children of {current_node_for_path.nodeid}: {[cd.BrowseName for cd in children_desc]}")
                        return None
                    current_node_for_path = next_node
                    logger.debug(f"OPCUAClient: Found part '{part_name}': {current_node_for_path.nodeid}")
                except ua.UaStatusCodeError as e_child:
                    logger.error(f"OPCUAClient: OPC UA Error getting child '{part_name}' (qualified: {qualified_part_name}) for path '{node_path_str}': {e_child} (Code: {e_child.code})")
                    children_desc = await current_node_for_path.get_children_descriptions()
                    logger.debug(f"OPCUAClient: Children of {current_node_for_path.nodeid} before error: {[cd.BrowseName for cd in children_desc]}")
                    return None
                except Exception as e_generic_child:
                    logger.exception(f"OPCUAClient: Unexpected error getting child '{part_name}' for path '{node_path_str}': {e_generic_child}")
                    return None

            logger.debug(f"OPCUAClient: Successfully found node for path '{node_path_str}': {current_node_for_path.nodeid}")
            return current_node_for_path

        except ua.UaStatusCodeError as e:
            logger.error(f"OPCUAClient: OPC UA Error finding node for path '{node_path_str}': {e} (Code: {e.code})")
            return None
        except Exception as e:
            logger.exception(f"OPCUAClient: Unexpected Error in get_node for path '{node_path_str}': {e}")
            return None

    async def read_value(self, node_identifier):
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
        if not self.is_connected:
            logger.warning("OPCUAClient: Write value called while not connected.")
            return False
        try:
            node = await self.get_node(node_identifier)
            if not node:
                # get_node already logs the error
                return False

            # Determine if this is one of the known problematic nodes
            is_eco_job_node = any(x in node_identifier for x in ["/Eco_iTaskType", "/Eco_iOrigination", "/Eco_iDestination"])
            
            if datatype:
                # Use explicitly provided datatype if given
                ua_value = ua.Variant(value, datatype)
            else:
                # Infer datatype if not provided, common types
                if isinstance(value, bool):
                    ua_value = ua.Variant(value, ua.VariantType.Boolean)
                elif isinstance(value, int):
                    if is_eco_job_node:
                        # Try Int32 for job-related nodes that have shown type mismatch issues
                        ua_value = ua.Variant(value, ua.VariantType.Int32)
                        logger.info(f"OPCUAClient: Using Int32 type for {node_identifier}")
                    else:
                        # Default to Int16 as often used in PLCs
                        ua_value = ua.Variant(value, ua.VariantType.Int16)
                elif isinstance(value, float):
                    ua_value = ua.Variant(value, ua.VariantType.Float)
                elif isinstance(value, str):
                    ua_value = ua.Variant(value, ua.VariantType.String)
                else:
                    ua_value = ua.Variant(value) # Let asyncua handle it

            logger.info(f"OPCUAClient: Writing value: {value} (UA Variant: {ua_value}) to {node_identifier}")
            
            try:
                await node.write_value(ua_value)
                logger.debug(f"OPCUAClient: Successfully wrote {value} to {node_identifier}")
                return True
            except ua.UaStatusCodeError as type_error:
                if "BadTypeMismatch" in str(type_error) and isinstance(value, int):
                    logger.info(f"OPCUAClient: Type mismatch. Trying alternative integer types for {node_identifier}")
                    # Try with a series of integer types
                    types_to_try = [
                        ua.VariantType.Int16, ua.VariantType.UInt16, 
                        ua.VariantType.Int32, ua.VariantType.UInt32,
                        ua.VariantType.Int64, ua.VariantType.UInt64
                    ]
                    
                    # If we already tried Int32 as our first guess, remove it from the list
                    if is_eco_job_node:
                        types_to_try.remove(ua.VariantType.Int32)
                    
                    for alt_type in types_to_try:
                        try:
                            logger.info(f"OPCUAClient: Trying with type {alt_type.name} for {node_identifier}")
                            alt_ua_value = ua.Variant(value, alt_type)
                            await node.write_value(alt_ua_value)
                            logger.info(f"OPCUAClient: Successfully wrote {value} with type {alt_type.name} to {node_identifier}")
                            return True
                        except Exception as alt_error:
                            logger.debug(f"OPCUAClient: Type {alt_type.name} failed for {node_identifier}: {alt_error}")
                            continue
                    
                    logger.error(f"OPCUAClient: All alternative types failed for {node_identifier}")
                    return False
                else:
                    # Re-raise the original error if it's not a type mismatch or not an integer
                    raise
        except ua.UaStatusCodeError as e:
            logger.error(f"OPCUAClient: OPC UA Error writing node {node_identifier} with value {value}: {e} (Code: {e.code})")
            return False
        except Exception as e:
            logger.exception(f"OPCUAClient: Unexpected Error writing node {node_identifier} with value {value}: {e}")
            return False

