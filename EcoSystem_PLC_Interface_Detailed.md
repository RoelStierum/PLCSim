# Interface Document: EcoSystemSim.py <=> PLC Communicatie (Gedetailleerde Paden)

Dit document beschrijft de OPC UA variabelen die worden uitgewisseld tussen het EcoSystem en de PLC. De volledige paden zijn  gebaseerd op de OPC UA structuur zoals geïmplementeerd in de PLC (`PLCSim_Pi.py`) en geverifieerd aan de hand van `EcoSystemSim.py` en `interface.txt`.

**Basis OPC UA Paden (aangenomen van `interface.txt`):**

- **EcoSystem naar PLC (EcoToPlc):** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"`
- **PLC naar EcoSystem (PlcToEco):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"`

In de onderstaande paden:

- `{ElevatorX}` wordt vervangen door `Elevator1` of `Elevator2`.
- `{AssignmentStructName}` wordt vervangen door `Elevator1EcoSystAssignment` voor `Elevator1` en `Elevator2EcoSystAssignment` voor `Elevator2`.
- `[x]` in `StationData[x]` representeert de index van het station (0 voor Elevator1, 1 voor Elevator2). De `EcoSystemSim.py` logica gebruikt `station_idx_for_opc_node` (0 of 1) wat overeenkomt met `Elevator{station_idx_for_opc_node + 1}` voor de assignment structuur, en direct voor `StationData` indexering.

## 1. EcoSystem naar PLC Variabelen (EcoToPlc)

Deze variabelen worden geschreven door het EcoSystem en gelezen door de PLC.

- **iTaskType (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator1"."Elevator1EcoSystAssignment"."iTaskType"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator2"."Elevator2EcoSystAssignment"."iTaskType"`
  - **Beschrijving:** Specificeert het type taak voor de PLC. (Data Type: Int64)
  - **Geschreven door:** `send_job`, `clear_task` (naar 0), `_reset_job_inputs_on_server_for_lift` (naar 0)

- **iOrigination (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator1"."Elevator1EcoSystAssignment"."iOrigination"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator2"."Elevator2EcoSystAssignment"."iOrigination"`
  - **Beschrijving:** Specificeert de oorsprongslocatie voor de taak. (Data Type: Int64)
  - **Geschreven door:** `send_job`, `_reset_job_inputs_on_server_for_lift` (naar 0)

- **iDestination (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator1"."Elevator1EcoSystAssignment"."iDestination"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator2"."Elevator2EcoSystAssignment"."iDestination"`
  - **Beschrijving:** Specificeert de bestemmingslocatie voor de taak. (Data Type: Int64)
  - **Geschreven door:** `send_job`, `_reset_job_inputs_on_server_for_lift` (naar 0)

- **xAcknowledgeMovement (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator1"."xAcknowledgeMovement"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator2"."xAcknowledgeMovement"`
  - **Beschrijving:** Signaal van EcoSystem om een PLC beweging/stap te bevestigen of om handshake te resetten. (Data Type: Boolean)
  - **Geschreven door:** `send_job` (naar `False`), `acknowledge_job_step` (naar `True`), `clear_task` (naar `False`)

- **iCancelAssignment / iCancelAssignent (per lift):**
  - **Pad Elevator1 (correct):** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator1"."iCancelAssignment"`
  - **Pad Elevator2 (correct):** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator2"."iCancelAssignment"`
  - **Beschrijving:** Signaal van EcoSystem om de huidige opdracht te annuleren, of gereset door het versturen/wissen van een taak. (Data Type: Int64)
  - **Geschreven door:** `send_job` (naar `0`), `clear_task` (naar `0`)

---
**Speciaal Geval: EcoSystem Overschrijft PLC-naar-Eco Variabele**
Het EcoSystem kan direct naar een variabele schrijven die normaal gesproken door de PLC beheerd wordt.

- **xTrayInElevator (per lift, geschreven door EcoSystem):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator1"."xTrayInElevator"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator2"."xTrayInElevator"`
  - **Beschrijving:** Simuleert/forceert de aanwezigheidsstatus van de tray in de lift. Gemaakt voor testen van Bring Away job.(Data Type: Boolean)
  - **Geschreven door:** `_toggle_tray_presence`

## 2. PLC naar EcoSystem Variabelen (PlcToEco)

Deze variabelen worden geschreven door de PLC en gelezen door het EcoSystem.

**Systeem-brede Station Data (onder `PlcToEco.StationDataToEco`):**

- **iAmountOfSations:**
  - **Pad:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationDataToEco"."iAmountOfSations"`
  - **Beschrijving:** Totaal aantal stations (liften) in het systeem. (Data Type: Int16)
  - **Gelezen door:** EcoSystem (algemene configuratie)
- **iMainStatus:**
  - **Pad:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationDataToEco"."iMainStatus"`
  - **Beschrijving:** Hoofdstatus van het gehele PLC systeem. (Data Type: Int16)
  - **Gelezen door:** EcoSystem (algemene status)

**StationData (Array, geïndexeerd per lift/station, onder `PlcToEco.StationData`):**
De `EcoSystemSim.py` gebruikt `StationData/{station_idx_for_opc}` waarbij `station_idx_for_opc` 0 is voor Lift1 en 1 voor Lift2. Dit komt overeen met `StationData[0]` en `StationData[1]`.
De PLC simulator maakt dit object aan onder `PlcToEco` als `StationData`.

- **iCycle (per station):**
  - **Pad Station 0 (Lift1):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[0]."iCycle"`
  - **Pad Station 1 (Lift2):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[1]."iCycle"`
  - **Beschrijving:** Huidig cyclenummer van het proces van het station.
  - **Gelezen door:** `_monitor_plc`

- **iStationStatus (per station):**
  - **Pad Station 0 (Lift1):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[0]."iStationStatus"`
  - **Pad Station 1 (Lift2):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[1]."iStationStatus"`
  - **Beschrijving:** Algemene status van het station. (GUI key `iStatus`)
  - **Gelezen door:** `_monitor_plc`

- **Handshake.iJobType (per station):**
  - **Pad Station 0 (Lift1):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[0]."Handshake"."iJobType"`
  - **Pad Station 1 (Lift2):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[1]."Handshake"."iJobType"`
  - **Beschrijving:** Type taak/bevestiging die de PLC verwacht of signaleert. (GUI key `iJobType`)
  - **Gelezen door:** `_monitor_plc`

- **iCancelAssignment (PLC naar Eco, per station):**
  - **Pad Station 0 (Lift1):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[0]."iCancelAssignment"`
  - **Pad Station 1 (Lift2):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[1]."iCancelAssignment"`
  - **Beschrijving:** Redencode indien een opdracht door de PLC is geannuleerd. (GUI key `iCancelAssignmentReasonCode`) (Data Type: Int16)
    - `1`: `CANCEL_PICKUP_WITH_TRAY` (Poging tot ophalen terwijl lift al een tray heeft)
    - `2`: `CANCEL_DESTINATION_OUT_OF_REACH` (Bestemming onbereikbaar)
    - `3`: `CANCEL_ORIGIN_OUT_OF_REACH` (Oorsprong onbereikbaar)
    - `4`: `CANCEL_INVALID_ZERO_POSITION` (Ongeldige nulpositie in opdracht)
    - `5`: `CANCEL_LIFTS_CROSS` (Potentiële botsing met andere lift)
    - `6`: `CANCEL_INVALID_ASSIGNMENT` (Algemene ongeldige opdracht)
    - `7`: `CANCEL_BY_ECOSYSTEM` (Opdracht geannuleerd op verzoek van EcoSystem)
  - **Gelezen door:** `_monitor_plc`

- **sShortAlarmDescription (per station):**
  - **Pad Station 0 (Lift1):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[0]."sShortAlarmDescription"`
  - **Pad Station 1 (Lift2):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[1]."sShortAlarmDescription"`
  - **Beschrijving:** Korte beschrijving van een actief alarm. (GUI key `sErrorShortDescription`)
  - **Gelezen door:** `_monitor_plc`

- **sAlarmSolution (per station):**
  - **Pad Station 0 (Lift1):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[0]."sAlarmSolution"`
  - **Pad Station 1 (Lift2):** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[1]."sAlarmSolution"`
  - **Beschrijving:** Voorgestelde oplossing voor een actief alarm. (GUI key `sErrorSolution`)
  - **Gelezen door:** `_monitor_plc`

**Lift Specifieke Data (direct onder `PlcToEco/{ElevatorX}/`):**

- **sSeq_Step_comment (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator1"."sSeq_Step_comment"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator2"."sSeq_Step_comment"`
  - **Beschrijving:** Commentaar/beschrijving van de huidige sequentiestap. (Ook gebruikt als GUI key `sErrorMessage`)
  - **Gelezen door:** `_monitor_plc`

- **iElevatorRowLocation (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator1"."iElevatorRowLocation"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator2"."iElevatorRowLocation"`
  - **Beschrijving:** Huidige rijlocatie van de lift.
  - **Gelezen door:** `_monitor_plc`

- **xTrayInElevator (per lift, gelezen door EcoSystem):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator1"."xTrayInElevator"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator2"."xTrayInElevator"`
  - **Beschrijving:** Boolean die aangeeft of er een tray in de lift aanwezig is.
  - **Gelezen door:** `_monitor_plc`

- **iCurrentForkSide (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator1"."iCurrentForkSide"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator2"."iCurrentForkSide"`
  - **Beschrijving:** Huidige zijde/positie van de vork van de lift.
  - **Gelezen door:** `_monitor_plc`

- **iErrorCode (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator1"."iErrorCode"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."Elevator2"."iErrorCode"`
  - **Beschrijving:** Huidige foutcode gerapporteerd door de lift.
  - **Gelezen door:** `_monitor_plc`

**Niet expliciet gemapt in `_monitor_plc` maar aanwezig in `interface.txt` (PlcToEco):**
Deze variabelen worden vermeld in `interface.txt` maar niet direct gebruikt in de `_monitor_plc` lees-loop van `EcoSystemSim.py` op basis van de huidige code. Ze kunnen relevant zijn voor de PLC-logica of andere delen van het systeem.

- `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationDataToEco"."iAmountOfSations"` (Nu hierboven gedocumenteerd)
- `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationDataToEco"."iMainStatus"` (Nu hierboven gedocumenteerd)
- `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[x]."sStationStateDescription"`
- `"Di_Call_Blocks"."OPC_UA"."PlcToEco"."StationData"[x]."Handshake"."iRowNr"`

**Niet expliciet gemapt in `EcoSystemSim.py` schrijf-logica maar aanwezig in `interface.txt` (EcoToPlc):**
Deze variabelen worden wel in `PLCSim.py` verwacht en/of gebruikt.

- **xWatchDog:**
  - **Pad:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."xWatchDog"`
  - **Beschrijving:** Watchdog signaal van EcoSystem naar PLC. PLC zet deze terug naar False na ontvangst. (Data Type: Boolean)
  - **Geschreven door:** EcoSystem (periodiek), **Gelezen door:** PLC
- **xClearError (per lift):**
  - **Pad Elevator1:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator1"."xClearError"`
  - **Pad Elevator2:** `"Di_Call_Blocks"."OPC_UA"."EcoToPlc"."Elevator2"."xClearError"`
  - **Beschrijving:** Signaal van EcoSystem om een actieve fout op de PLC te wissen. PLC zet deze terug naar False na verwerking. (Data Type: Boolean)
  - **Geschreven door:** EcoSystem (op actie gebruiker), **Gelezen door:** PLC
