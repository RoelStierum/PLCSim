import asyncio
import logging

from asyncua import Server, ua
from asyncua.common.methods import uamethod


@uamethod
def func(parent, value):
    return value * 2


async def main():
    _logger = logging.getLogger(__name__)
    # setup our server
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://0.0.0.0:4840/freeopcua/server/")

    # set up our own namespace, not really necessary but should as spec
    uri = "http://examples.freeopcua.github.io"
    idx = await server.register_namespace(uri)

    # populating our address space
    # server.nodes, contains links to very common nodes like objects and root
    myobj = await server.nodes.objects.add_object(idx, "MyObject")
    myvar = await myobj.add_variable(idx, "MyVariable", 6.7)
    # Set MyVariable to be writable by clients
    await myvar.set_writable()
    await server.nodes.objects.add_method(
        ua.NodeId("ServerMethod", idx),
        ua.QualifiedName("ServerMethod", idx),
        func,
        [ua.VariantType.Int64],
        [ua.VariantType.Int64],
    )
    _logger.info("Starting server!")
    async with server:
        while True:
            await asyncio.sleep(1)
            new_val = await myvar.get_value() + 0.1
            _logger.info("Set value of %s to %.1f", myvar, new_val)
            await myvar.write_value(new_val)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main(), debug=True)
Before we even look at the code in detail, let’s try out what our server can do. Start the server in a terminal with python server-minimal.py and open a new console. In the new console you now can use the CLI tools (see Command Line Tools) provided by the package to explore the server. The following session gives you an idea how the tools can be used.

uals --url=opc.tcp://127.0.0.1:4840  # List root node
Browsing node i=84 at opc.tcp://127.0.0.1:4840
DisplayName                                NodeId   BrowseName    Value

LocalizedText(Locale=None, Text='Objects') i=85     0:Objects
LocalizedText(Locale=None, Text='Types')   i=86     0:Types
LocalizedText(Locale=None, Text='Views')   i=87     0:Views

uals --url=opc.tcp://127.0.0.1:4840 --nodeid i=85 # List 0:Objects
Browsing node i=85 at opc.tcp://127.0.0.1:4840

DisplayName                                     NodeId               BrowseName         Value

LocalizedText(Locale=None, Text='Server')       i=2253               0:Server
LocalizedText(Locale=None, Text='Aliases')      i=23470              0:Aliases
LocalizedText(Locale=None, Text='MyObject')     ns=2;i=1             2:MyObject
LocalizedText(Locale=None, Text='ServerMethod') ns=2;s=ServerMethod  2:ServerMethod

# In the last two lines we can see our own MyObject and ServerMethod
# Lets read a value!
uaread --url=opc.tcp://127.0.0.1:4840 --nodeid "ns=2;i=2"  # By NodeId
7.599999999999997
uaread --url=opc.tcp://127.0.0.1:4840 --path "0:Objects,2:MyObject,2:MyVariable" # By BrowsePath
12.199999999999996