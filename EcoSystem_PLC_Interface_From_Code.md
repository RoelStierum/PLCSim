# Interface Document: EcoSystemSim.py <=> PLC (Afgeleid uit Code)

Dit document beschrijft de OPC UA variabelen die worden uitgewisseld tussen de `EcoSystemSim.py` applicatie (EcoSystem) en de PLC. De paden zijn rechtstreeks afgeleid van hoe ze in de `EcoSystemSim.py` broncode worden geconstrueerd.

**Basis OPC UA Paden (zoals gedefinieerd in `EcoSystemGUI_DualLift_ST`):**
-   `self.ECO_TO_PLC_BASE` = `"GVL_OPC/EcoToPlc"`
-   `self.PLC_TO_ECO_BASE` = `"GVL_OPC/PlcToEco"`

**Conversie naar "Di_Call_Blocks" Notatie:**
De hieronder getoonde paden gebruiken de "Di_Call_Blocks" notatie, waarbij de door de code gegenereerde string (bijv. `GVL_OPC/EcoToPlc/Elevator1/...`) wordt geprefixt met `\\"Di_Call_Blocks\\".\\"OPC_UA\\".` en slashes worden vervangen door punten tussen quotes.

**Definities:**
-   `{elevator_id_str}`: "Elevator1" voor LIFT1_ID, "Elevator2" voor LIFT2_ID.
-   `{station_idx_for_opc}`: 0 voor LIFT1_ID, 1 voor LIFT2_ID.
-   `{AssignmentStructName}`: `Elevator{station_idx_for_opc_node + 1}EcoSystAssignment` (bijv. "Elevator1EcoSystAssignment").

## 1. EcoSystem naar PLC Variabelen (EcoToPlc)

Geschreven door het EcoSystem, gelezen door de PLC.
Basis pad-constructie in code: `f"{self.ECO_TO_PLC_BASE}/..."`

---
**Variabelen binnen `ElevatorXEcoSystAssignment` structuur:**
Pad-constructie in code: `f"{self.ECO_TO_PLC_BASE}/{elevator_id_str}/Elevator{station_idx_for_opc_node + 1}EcoSystAssignment/{variable_name}"`

-   **iTaskType:**
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator1"."Elevator1EcoSystAssignment"."iTaskType"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator2"."Elevator2EcoSystAssignment"."iTaskType"`
    -   **Data Type:** Int64
    -   **Geschreven door:** `send_job`, `clear_task` (naar 0), `_reset_job_inputs_on_server_for_lift` (naar 0)

-   **iOrigination:**
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator1"."Elevator1EcoSystAssignment"."iOrigination"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator2"."Elevator2EcoSystAssignment"."iOrigination"`
    -   **Data Type:** Int64
    -   **Geschreven door:** `send_job`, `_reset_job_inputs_on_server_for_lift` (naar 0)

-   **iDestination:**
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator1"."Elevator1EcoSystAssignment"."iDestination"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator2"."Elevator2EcoSystAssignment"."iDestination"`
    -   **Data Type:** Int64
    -   **Geschreven door:** `send_job`, `_reset_job_inputs_on_server_for_lift` (naar 0)

---
**Variabelen direct onder `ElevatorX` structuur:**
Pad-constructie in code: `f"{self.ECO_TO_PLC_BASE}/{elevator_id_str}/{variable_name}"`

-   **xAcknowledgeMovement:**
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator1"."xAcknowledgeMovement"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator2"."xAcknowledgeMovement"`
    -   **Data Type:** Boolean
    -   **Geschreven door:** `send_job` (naar `False`), `acknowledge_job_step` (naar `True`), `clear_task` (naar `False`)

-   **iCancelAssignment / iCancelAssignent (typo voor Lift1):**
    -   **Pad (Lift1 - met typo):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator1"."iCancelAssignent"`
    -   **Pad (Lift2 - correct):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."EcoToPlc"."Elevator2"."iCancelAssignment"`
    -   **Data Type:** Int64
    -   **Geschreven door:** `send_job` (naar `0`), `clear_task` (naar `0`)

---
**Speciaal Geval: EcoSystem Overschrijft PLC-naar-Eco Variabele**
Pad-constructie in code: `f"{self.PLC_TO_ECO_BASE}/{elevator_id_str}/xTrayInElevator"`

-   **xTrayInElevator (geschreven door EcoSystem):**
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator1"."xTrayInElevator"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator2"."xTrayInElevator"`
    -   **Data Type:** Boolean
    -   **Geschreven door:** `_toggle_tray_presence`

## 2. PLC naar EcoSystem Variabelen (PlcToEco)

Geschreven door de PLC, gelezen door het EcoSystem (`_monitor_plc` methode).

---
**Variabelen onder `StationData` (geïndexeerd per lift/station):**
Pad-constructie in code: `f"{self.PLC_TO_ECO_BASE}/StationData/{station_idx_for_opc}/{variable_name_in_map}"`

-   **iCycle:** (GUI Key: "iCycle")
    -   **Pad (Lift1/Station 0):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."0"."iCycle"`
    -   **Pad (Lift2/Station 1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."1"."iCycle"`
    -   **Gelezen door:** `_monitor_plc`

-   **iStationStatus:** (GUI Key: "iStatus")
    -   **Pad (Lift1/Station 0):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."0"."iStationStatus"`
    -   **Pad (Lift2/Station 1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."1"."iStationStatus"`
    -   **Gelezen door:** `_monitor_plc`

-   **Handshake/iJobType:** (GUI Key: "iJobType")
    -   **Pad (Lift1/Station 0):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."0"."Handshake"."iJobType"`
    -   **Pad (Lift2/Station 1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."1"."Handshake"."iJobType"`
    -   **Gelezen door:** `_monitor_plc`

-   **iCancelAssignment:** (GUI Key: "iCancelAssignmentReasonCode")
    -   **Pad (Lift1/Station 0):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."0"."iCancelAssignment"`
    -   **Pad (Lift2/Station 1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."1"."iCancelAssignment"`
    -   **Gelezen door:** `_monitor_plc`

-   **sShortAlarmDescription:** (GUI Key: "sErrorShortDescription")
    -   **Pad (Lift1/Station 0):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."0"."sShortAlarmDescription"`
    -   **Pad (Lift2/Station 1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."1"."sShortAlarmDescription"`
    -   **Gelezen door:** `_monitor_plc`

-   **sAlarmSolution:** (GUI Key: "sErrorSolution")
    -   **Pad (Lift1/Station 0):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."0"."sAlarmSolution"`
    -   **Pad (Lift2/Station 1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."StationData"."1"."sAlarmSolution"`
    -   **Gelezen door:** `_monitor_plc`

---
**Variabelen direct onder `ElevatorX` structuur (PlcToEco):**
Pad-constructie in code: `f"{self.PLC_TO_ECO_BASE}/{elevator_id_str}/{variable_name_in_map}"`

-   **sSeq_Step_comment:** (GUI Keys: "sSeq_Step_comment", "sErrorMessage")
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator1"."sSeq_Step_comment"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator2"."sSeq_Step_comment"`
    -   **Gelezen door:** `_monitor_plc`

-   **iElevatorRowLocation:** (GUI Key: "iElevatorRowLocation")
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator1"."iElevatorRowLocation"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator2"."iElevatorRowLocation"`
    -   **Gelezen door:** `_monitor_plc`

-   **xTrayInElevator:** (GUI Key: "xTrayInElevator")
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator1"."xTrayInElevator"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator2"."xTrayInElevator"`
    -   **Gelezen door:** `_monitor_plc` (Let op: EcoSystem kan dit ook schrijven, zie Speciaal Geval hierboven)

-   **iCurrentForkSide:** (GUI Key: "iCurrentForkSide")
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator1"."iCurrentForkSide"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator2"."iCurrentForkSide"`
    -   **Gelezen door:** `_monitor_plc`

-   **iErrorCode:** (GUI Key: "iErrorCode")
    -   **Pad (Lift1):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator1"."iErrorCode"`
    -   **Pad (Lift2):** `"Di_Call_Blocks"."OPC_UA"."GVL_OPC"."PlcToEco"."Elevator2"."iErrorCode"`
    -   **Gelezen door:** `_monitor_plc`

