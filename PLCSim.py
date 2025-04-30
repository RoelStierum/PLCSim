from asyncua import Server, ua
from asyncua.common.node import Node
from datetime import datetime
import asyncio
from enum import Enum
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SystemMode(Enum):
    MANUAL = 0
    AUTOMATIC = 1
    MAINTENANCE = 2

class TaskType(Enum):
    NONE = 0
    PICK = 1
    PLACE = 2
    MOVE = 3

class Status(Enum):
    IDLE = 0
    BUSY = 1
    ERROR = 2
    COMPLETED = 3

class PLCSimulator:
    def __init__(self):
        self.server = None
        self.namespace = None
        self.variables = {}
        self.status = Status.IDLE
        self.task_type = TaskType.NONE
        self.system_mode = SystemMode.MANUAL
        self.error_message = ""

    async def initialize(self):
        try:
            # Initialize server
            self.server = Server()
            
            # Set endpoint and server name
            await self.server.init()
            self.server.set_endpoint("opc.tcp://127.0.0.1:4860")
            self.server.set_server_name("PLCSimulator")
            
            # Set security policy to allow open connections
            self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
            
            # Register namespace
            uri = "http://plcsim.example.com"
            self.namespace = await self.server.register_namespace(uri)
            logger.info(f"Namespace registered with index {self.namespace}")
            
            # Create objects and variables
            objects = self.server.nodes.objects
            plc_node = await objects.add_object(self.namespace, "PLC")
            
            # Add variables with proper data types and make them writable
            self.variables["status"] = await plc_node.add_variable(self.namespace, "iStatus", ua.Variant(0, ua.VariantType.Int32))
            self.variables["task_type"] = await plc_node.add_variable(self.namespace, "iTaskType", ua.Variant(0, ua.VariantType.Int32))
            self.variables["system_mode"] = await plc_node.add_variable(self.namespace, "iMainStatus", ua.Variant(0, ua.VariantType.Int32))
            self.variables["error_message"] = await plc_node.add_variable(self.namespace, "sAlarmMessage", ua.Variant("", ua.VariantType.String))
            
            # Add additional variables needed by EcoSystemSim
            self.variables["origination"] = await plc_node.add_variable(self.namespace, "iOrigination", ua.Variant(0, ua.VariantType.Int32))
            self.variables["destination"] = await plc_node.add_variable(self.namespace, "iDestination", ua.Variant(0, ua.VariantType.Int32))
            self.variables["station_status"] = await plc_node.add_variable(self.namespace, "iStationStatus", ua.Variant(0, ua.VariantType.Int32))
            self.variables["short_alarm"] = await plc_node.add_variable(self.namespace, "sShortAlarmDescription", ua.Variant("", ua.VariantType.String))
            self.variables["alarm_solution"] = await plc_node.add_variable(self.namespace, "sAlarmSolution", ua.Variant("", ua.VariantType.String))
            self.variables["watchdog"] = await plc_node.add_variable(self.namespace, "xWatchDog", ua.Variant(False, ua.VariantType.Boolean))
            self.variables["tray_in_elevator"] = await plc_node.add_variable(self.namespace, "xTrayInElevator", ua.Variant(False, ua.VariantType.Boolean))
            self.variables["ack_movement"] = await plc_node.add_variable(self.namespace, "xAcknowledgeMovement", ua.Variant(False, ua.VariantType.Boolean))
            
            # Make variables writable
            for var in self.variables.values():
                await var.set_writable()
            
            # Set initial values
            await self.variables["status"].write_value(ua.Variant(self.status.value, ua.VariantType.Int32))
            await self.variables["task_type"].write_value(ua.Variant(self.task_type.value, ua.VariantType.Int32))
            await self.variables["system_mode"].write_value(ua.Variant(self.system_mode.value, ua.VariantType.Int32))
            await self.variables["error_message"].write_value(ua.Variant(self.error_message, ua.VariantType.String))
            
            # Start server
            await self.server.start()
            logger.info(f"Server started at {self.server.endpoint}")
            
        except Exception as e:
            logger.error(f"Error initializing server: {e}")
            if self.server:
                try:
                    await self.server.stop()
                except:
                    pass
            raise

    async def update_status(self, new_status: Status):
        try:
            self.status = new_status
            await self.variables["status"].write_value(ua.Variant(new_status.value, ua.VariantType.Int32))
            logger.info(f"Status updated to {new_status.name}")
        except Exception as e:
            logger.error(f"Error updating status: {e}")
            raise

    async def update_task_type(self, new_task_type: TaskType):
        try:
            self.task_type = new_task_type
            await self.variables["task_type"].write_value(ua.Variant(new_task_type.value, ua.VariantType.Int32))
            logger.info(f"Task type updated to {new_task_type.name}")
        except Exception as e:
            logger.error(f"Error updating task type: {e}")
            raise

    async def send_error_message(self, message: str):
        try:
            self.error_message = message
            await self.variables["error_message"].write_value(ua.Variant(message, ua.VariantType.String))
            logger.info(f"Error message updated: {message}")
        except Exception as e:
            logger.error(f"Error sending error message: {e}")
            raise

    async def run(self):
        try:
            # Toggle watchdog every second to indicate system is alive
            watchdog_value = False
            
            while True:
                # Toggle watchdog
                watchdog_value = not watchdog_value
                await self.variables["watchdog"].write_value(ua.Variant(watchdog_value, ua.VariantType.Boolean))
                
                # Simulate PLC behavior
                if self.status == Status.BUSY:
                    # Simulate task execution
                    await asyncio.sleep(2)
                    await self.update_status(Status.COMPLETED)
                
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            raise

async def main():
    plc = PLCSimulator()
    try:
        await plc.initialize()
        logger.info("PLC simulator initialized successfully")
        await plc.run()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        if plc.server:
            await plc.server.stop()
    except Exception as e:
        logger.error(f"Error in main: {e}")
        if plc.server:
            try:
                await plc.server.stop()
            except:
                pass

if __name__ == "__main__":
    asyncio.run(main())