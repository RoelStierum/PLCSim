# Gids voor EcoSystem Software Ontwikkeling: Implementatie OPC UA Client voor PLC Integratie

**Datum:** 16 mei 2025
**Doelgroep:** Software Ontwikkelteam van het Officiële EcoSystem
**Referentie PLC Implementatie (Server):** `PLCSim.py` (simuleert de PLC als OPC UA Server)
**Referentie EcoSystem Implementatie (Client):** `EcoSystemSim.py` en `opcua_client.py` (deze componenten fungeren als OPC UA Client)

## 1. Inleiding

Dit document biedt richtlijnen voor het software ontwikkelteam van het officiële EcoSystem voor de implementatie van de **OPC UA Client interface**. Het doel is om een naadloze integratie mogelijk te maken met een PLC (Programmable Logic Controller), die ontwikkeld wordt door de afdeling Systems and Control en fungeert als de **OPC UA Server**.

De hier beschreven interface en interacties zijn gebaseerd op:

- De OPC UA Server structuur zoals gedefinieerd en gesimuleerd in `PLCSim.py`.
- De client-side logica en data-uitwisseling zoals gedemonstreerd in `EcoSystemSim.py` en `opcua_client.py`.

In deze architectuur fungeert de **PLC als de OPC UA Server**, en het **EcoSystem als de OPC UA Client**. Deze gids beschrijft de benodigde clientconfiguratie, data-interacties en communicatieprotocollen die het EcoSystem moet implementeren om met de PLC Server te communiceren.

## 2. Algemene Vereisten voor de EcoSystem OPC UA Client

De OPC UA Client van het EcoSystem moet aan de volgende eisen voldoen:

- Implementeer een volwaardige OPC UA Client.
- Kan verbinding maken met een gespecificeerd OPC UA Server endpoint (van de PLC).
- Kan de Namespace URI van de PLC Server gebruiken om de correcte Namespace Index te vinden.
- Kan de gespecificeerde OPC UA nodes (variabelen) op de PLC Server lezen en/of beschrijven met de correcte paden en datatypes.
- Kan de gespecificeerde datatypes voor de variabelen correct interpreteren en versturen.

## 3. OPC UA Client Configuratie (EcoSystem-zijde)

De volgende parameters zijn essentieel voor de configuratie van de OPC UA Client binnen het EcoSystem:

- **PLC OPC UA Server Endpoint URL:**
  - Het EcoSystem moet geconfigureerd kunnen worden met het endpoint URL van de PLC OPC UA Server.
  - Voorbeeld (uit `PLCSim.py`): `opc.tcp://127.0.0.1:4860/gibas/plc/` en voor de simulator op de raspberry: `opc.tcp://192.168.137.2:4860/gibas/plc/`(dit zal in de productieomgeving het adres van de fysieke PLC zijn).
  - Het EcoSystem (client) maakt verbinding met *dit adres*.

- **Namespace URI (van de PLC Server):**
  - De variabelen die de PLC Server exposeert, bevinden zich binnen een specifieke Namespace.
  - De PLC-simulatie (`PLCSim.py`) en de client-code (`opcua_client.py`) gebruiken `PLC_NS_URI = "http://gibas.com/plc/"`.
  - De EcoSystem Client moet deze URI gebruiken om de corresponderende Namespace Index op de PLC Server dynamisch te vinden om correct naar de variabelen te kunnen verwijzen.

## 4. Data Interface Specificaties (EcoSystem Client Interactie met PLC Server)

De EcoSystem OPC UA Client moet interageren met de datastructuur die de PLC OPC UA Server aanbiedt. Dit betekent het lezen van en schrijven naar de OPC UA nodes (variabelen) op de PLC Server.

Raadpleeg het document `EcoSystem_PLC_Interface.md` voor de volledige, gedetailleerde OPC UA-paden (NodeIds) en hun datatypes. Deze paden en datatypes zijn gedefinieerd op de PLC Server (zoals in `PLCSim.py`) en het EcoSystem (client) moet deze gebruiken. De paden volgen de notatie als in de PLC `"Di_Call_Blocks"."OPC_UA"....`.

### 4.1. Data die het EcoSystem *Schrijft naar de PLC Server* (PLC Server Nodes die EcoSystem beschrijft)

Deze sectie beschrijft de variabelen op de PLC Server waarnaar de EcoSystem Client data zal schrijven. Dit zijn typisch commando's, taakinformatie of configuraties die van het EcoSystem naar de PLC gaan. Vanuit het perspectief van de PLC Server zijn dit de `EcoToPlc` variabelen.

- **Taakcommando's per Lift/Station (binnen `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."ElevatorX"."ElevatorXEcoSystAssignment"` structuur op de PLC Server):**
  - `iTaskType`: EcoSystem schrijft het type taak (bijv. Full, MoveTo, PreparePickUp, BringAway).
  - `iOrigination`: EcoSystem schrijft de oorsprongslocatie.
  - `iDestination`: EcoSystem schrijft de bestemmingslocatie.
- **Handshake en Controle signalen (direct onder `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."ElevatorX"` op de PLC Server):**
  - `xAcknowledgeMovement`: EcoSystem schrijft `True` om een PLC-actie te bevestigen, of `False` bij een nieuwe job/clear.
  - `iCancelAssignment`: EcoSystem schrijft een waarde om de huidige taak op de PLC te annuleren.
    - *Attentie Typo:* De PLC-simulatie (`PLCSim.py`) verwacht mogelijk `iCancelAssignent` (met 'ent') voor `Elevator1` onder `EcoToPlc`. Het EcoSystem (client) moet schrijven naar de node zoals deze op de PLC server is geïmplementeerd. Idealiter wordt dit op de PLC server gecorrigeerd naar `iCancelAssignment`.
- **Watchdog (onder `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"` op de PLC Server):**
  - `xWatchDog`: Indien een watchdog mechanisme is geïmplementeerd, kan het EcoSystem periodiek naar deze variabele op de PLC Server schrijven om aan te geven dat het actief is. De `PLCSim.py` definieert deze variabele, maar de client (`EcoSystemSim.py`) schrijft er momenteel niet actief naar.

**Voorbeeld Pad (EcoSystem Client schrijft naar PLC Server):**
De EcoSystem Client schrijft bijvoorbeeld naar de volgende node op de PLC Server:
`"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator1"."Elevator1EcoSystAssignment"."iTaskType"`

### 4.2. Data die het EcoSystem *Leest van de PLC Server* (PLC Server Nodes die EcoSystem leest)

Deze sectie beschrijft de variabelen op de PLC Server die de EcoSystem Client zal lezen. Dit zijn typisch statusupdates, sensordata of alarmen van de PLC. Vanuit het perspectief van de PLC Server zijn dit de `PlcToEco` variabelen.

- **Statusinformatie per Lift/Station (binnen `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."ElevatorX"` of `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData[X]"` op de PLC Server):**
  - `iCycle`: EcoSystem leest het huidige cyclenummer van de PLC.
  - `iStationStatus`: EcoSystem leest de algemene status van het station/de lift.
  - `Handshake/iJobType`: EcoSystem leest het type taak/bevestiging die de PLC signaleert.
  - `iCancelAssignment`: EcoSystem leest de redencode indien de PLC een taak annuleert.
  - `sShortAlarmDescription`: EcoSystem leest een korte tekstuele beschrijving van een actueel alarm van de PLC.
  - `sAlarmSolution`: EcoSystem leest een voorgestelde oplossing voor het actuele alarm van de PLC.
- **Lift-specifieke Data (binnen `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."ElevatorX"` op de PLC Server):**
  - `sSeq_Step_comment`: EcoSystem leest commentaar bij de huidige stap in de PLC-sequentie.
  - `iElevatorRowLocation`: EcoSystem leest de huidige rijlocatie van de lift.
  - `xTrayInElevator`: EcoSystem leest boolean die aangeeft of er een tray in de lift aanwezig is.
  - `iCurrentForkSide`: EcoSystem leest de positie van de vork.
  - `iErrorCode`: EcoSystem leest de foutcode van de PLC.
- **Systeem Status (binnen `"Di_Call_Blocks"."OPC_UA"."PlcToEco"` op de PLC Server):**
  - `iAmountOfSations`: EcoSystem leest het aantal stations.
  - `iMainStatus`: EcoSystem leest de hoofdstatus van het PLC systeem.

**Voorbeeld Pad (EcoSystem Client leest van PLC Server):**
De EcoSystem Client leest bijvoorbeeld van de volgende node op de PLC Server:
`"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator1"."iElevatorRowLocation"`

## 5. Belangrijke Communicatie Aspecten (EcoSystem Client Implementatie)

De EcoSystem Client logica moet de volgende communicatiepatronen correct afhandelen in interactie met de PLC Server:

- **Handshake (`xAcknowledgeMovement`):**
  - De EcoSystem Client **schrijft** `False` naar `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."ElevatorX"."xAcknowledgeMovement"` op de PLC Server bij het sturen van een nieuwe job of bij het clearen/annuleren van een taak.
  - Nadat de PLC een stap van de job heeft voltooid (en dit signaleert via haar eigen statusvariabelen), **schrijft** de EcoSystem Client `True` naar `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."ElevatorX"."xAcknowledgeMovement"` op de PLC Server om de PLC actie te bevestigen.
  - De EcoSystem Client moet de status van de PLC (bijv. `iJobType` of specifieke stapvariabelen) **lezen** om te bepalen wanneer een acknowledge gestuurd moet worden.

- **Taakverwerking:**
  - De EcoSystem Client is verantwoordelijk voor het correct **schrijven** van `iTaskType`, `iOrigination`, en `iDestination` naar de respectievelijke nodes onder `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"...` op de PLC Server wanneer een nieuwe taak naar de PLC wordt gestuurd.

- **Foutafhandeling:**
  - De EcoSystem Client moet voorbereid zijn om `iErrorCode`, `sShortAlarmDescription`, en `sAlarmSolution` van de `"Di_Call_Blocks"."OPC_UA"."PlcToEco"...` nodes op de PLC Server te **lezen** en hierop adequaat te reageren (bijv. logging, tonen in UI).

- **Annuleringen:**
  - De EcoSystem Client **schrijft** naar `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."ElevatorX"."iCancelAssignment"` op de PLC Server om een taak bij de PLC te annuleren.
  - De EcoSystem Client **leest** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."ElevatorX"."iCancelAssignment"` (of `StationData[X].iCancelAssignment`) van de PLC Server om te weten of en waarom de PLC een taak heeft geannuleerd.

- **Datatypes:**
  - De EcoSystem Client **moet** data lezen en schrijven met de correcte OPC UA datatypes zoals gespecificeerd door de PLC Server (zie `EcoSystem_PLC_Interface_From_Code.md`). Consistentie met `opcua_client.py` is hierbij een goede richtlijn. Bijvoorbeeld: `Int16`, `Int32`, `Int64`, `Boolean`, `String`.

## 6. Stappenplan voor EcoSystem Client Implementatie (Algemeen)

1. **OPC UA Client Stack Keuze:** Selecteer en configureer een geschikte OPC UA client software stack of bibliotheek voor de programmeertaal/omgeving van het EcoSystem. (`asyncua` wordt gebruikt in de referentiesimulaties).
2. **Verbindingsconfiguratie:**
   - Implementeer de mogelijkheid om het PLC OPC UA Server Endpoint URL te configureren.
   - Implementeer logica om verbinding te maken met de PLC Server.
3. **Namespace Handling:**
   - Gebruik de Namespace URI (`http://gibas.com/plc/`) om de correcte Namespace Index op de PLC Server te verkrijgen na het verbinden.
4. **Node Interactie:**
   - Implementeer functies om OPC UA nodes op de PLC Server te lezen op basis van hun NodeId (opgebouwd met de verkregen Namespace Index en de string paden uit `EcoSystem_PLC_Interface_From_Code.md`).
   - Implementeer functies om naar OPC UA nodes op de PLC Server te schrijven.
5. **Applicatielogica Implementeren:**
   - Ontwikkel de logica binnen het EcoSystem om commando's (zoals nieuwe jobs, acknowledgements) te vertalen naar schrijfacties op de correcte PLC Server nodes.
   - Ontwikkel de logica om statusinformatie, alarmen, etc., van de PLC Server nodes te lezen en te verwerken.
   - Implementeer de handshake logica en andere controle signalen zoals beschreven in sectie 5.
6. **Testen en Validatie:**
   - Test de verbinding met de PLC Server (initieel met `PLCSim.py`).
   - Verifieer dat het lezen en schrijven van alle gespecificeerde variabelen correct functioneert.
   - Test de volledige communicatieflows (job start, acknowledge, completion, annulering, foutafhandeling).
   - Voer integratietesten uit met de daadwerkelijke PLC zodra deze beschikbaar is en als OPC UA Server draait.

## 7. Aandachtspunten voor het EcoSystem Team

- **PLC is de Server:** De PLC definieert de OPC UA interface (nodes, datatypes, structuur). Het EcoSystem (client) moet zich hieraan conformeren.
- **Consistentie is Cruciaal:** De EcoSystem Client moet exact de NodeIds (paden), variabelenamen en datatypes gebruiken zoals de PLC Server deze exposeert. Elke afwijking kan leiden tot communicatiefouten.
- **Namespace Index:** Het correct verkrijgen en gebruiken van de Namespace Index van de PLC Server is essentieel.
- **Typo `iCancelAssignent` vs `iCancelAssignment`:** Wees alert op deze mogelijke typo in de `EcoToPlc` structuur op de PLC Server. De client moet interageren met de daadwerkelijk geïmplementeerde naam op de server. Het is aan te raden dit op de PLC server te standaardiseren naar `iCancelAssignment`.
- **Watchdog (`xWatchDog`):** Bepaal of het EcoSystem de `EcoToPlc.xWatchDog` variabele op de PLC Server periodiek moet beschrijven als een "levenspuls". Dit hangt af van de vereisten voor robuustheid.
- **Documentatie Verwijzingen:** Gebruik `EcoSystem_PLC_Interface_From_Code.md` als de primaire bron voor de exacte OPC UA paden en datatypes die de PLC Server aanbiedt. `PLCSim.py` is de referentie voor de server-side implementatie van deze interface.

Dit document dient als een startpunt voor de implementatie van de EcoSystem OPC UA Client. Regelmatige afstemming tussen het EcoSystem ontwikkelteam, het Systems and Control team (PLC), en de ontwikkelaar van de PLC-simulatie (`PLCSim.py`) wordt aanbevolen gedurende het ontwikkelproces.
