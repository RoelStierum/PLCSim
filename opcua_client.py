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

            eco_specific_parts = [
                "Eco_iTaskType", "Eco_iOrigination", "Eco_iDestination",
                "EcoAck_iAssingmentType", "EcoAck_iRowNr", "EcoAck_xAcknowldeFromEco"
            ]
            # Check if any of the eco_specific_parts are present in the node_identifier string
            is_eco_job_node = any(part in node_identifier for part in eco_specific_parts)
            
            ua_variant_to_write = None

            # Verwijder de specifieke Int64-forceringsregel voor is_eco_job_node.
            # Laat de generieke type-inferentie en fallback dit afhandelen.
            if datatype: # Als een expliciet datatype is meegegeven
                ua_variant_to_write = ua.Variant(value, datatype)
                logger.info(f"OPCUAClient: Using provided datatype {datatype.name} for {node_identifier} (value: {value}).")
            else:
                # Infer datatype if not provided
                if isinstance(value, bool):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Boolean)
                elif isinstance(value, int): 
                    # Standaard naar Int32 voor integers als geen specifiek type is gegeven.
                    # De PLC definieert Eco-nodes als Int16, dus de fallback zal dit corrigeren.
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Int32) 
                elif isinstance(value, float):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.Float)
                elif isinstance(value, str):
                    ua_variant_to_write = ua.Variant(value, ua.VariantType.String)
                else:
                    ua_variant_to_write = ua.Variant(value) # Let asyncua infer
                logger.info(f"OPCUAClient: Inferred datatype {ua_variant_to_write.VariantType.name} for {node_identifier} (value: {value}).")

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

                    # Geen speciale behandeling meer voor is_eco_job_node hier, de lijst is alomvattend.

                    if not types_to_try:
                        logger.warning(f"OPCUAClient: No alternative integer types left after initial attempt with {initial_type_used_for_write_attempt.name} for {node_identifier}.")
                        # Fall through to log the error and return False

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
                                # break # Optionally stop if it's not a type mismatch
                        except Exception as alt_error:
                            logger.debug(f"OPCUAClient: Other error trying alt type {alt_type.name} for {node_identifier}: {alt_error}")
                    
                    logger.error(f"OPCUAClient: All alternative integer types failed for {node_identifier}. Initial type was {initial_type_used_for_write_attempt.name}. Last error: {type_error}")
                    return False 
                else: 
                    # This branch is for:
                    # 1. "BadTypeMismatch" for an Eco node where Int64 was already tried (should be rare, indicates PLC expects something else entirely).
                    # 2. "BadTypeMismatch" for non-integer values.
                    # 3. Other UaStatusCodeErrors (not "BadTypeMismatch").
                    logger.error(f"OPCUAClient: Unhandled OPC UA Error for {node_identifier} (Value: {value}, Type attempted: {initial_type_used_for_write_attempt.name}): {type_error}")
                    return False
        except ua.UaStatusCodeError as e:
            logger.error(f"OPCUAClient: OPC UA Error writing node {node_identifier} with value {value}: {e} (Code: {e.code})")
            return False
        except Exception as e:
            logger.exception(f"OPCUAClient: Unexpected Error writing node {node_identifier} with value {value}: {e}")
            return False

