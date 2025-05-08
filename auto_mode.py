import tkinter as tk
from tkinter import ttk
import asyncio
import logging
import random
import time
from asyncua import ua
from lift_visualization import LIFT1_ID, LIFT2_ID, LIFTS, MAX_ROWS_LEFT, MAX_ROWS_RIGHT

# Stel de logger in
logger = logging.getLogger("AutoMode")

class AutoModeManager:
    def __init__(self, ecosystem_gui):
        self.eco_gui = ecosystem_gui
        self.running = False
        self.auto_task = None
        self.last_job_time = {lift_id: 0 for lift_id in LIFTS}
        self.min_wait_between_jobs = 2.0  # Seconden
        self.max_wait_between_jobs = 8.0  # Seconden
        self.error_recovery_time = 5.0  # Seconden
        
        # Bijhouden of een lift bezet is (bezig met een taak)
        self.lift_busy = {lift_id: False for lift_id in LIFTS}
        
        # Zones voor elke lift om botsingen te voorkomen
        self.lift_zones = {
            LIFT1_ID: {'origin_range': (1, 99), 'dest_range': (1, 99)},  # Lift1 werkt met lagere nummers
            LIFT2_ID: {'origin_range': (1, 99), 'dest_range': (1, 99)}  # Lift2 werkt met hogere nummers
        }
        
        # Voeg de GUI-elementen toe
        self._setup_ui()
    
    def _setup_ui(self):
        """Voegt auto-mode besturingselementen toe aan de EcoSystem GUI"""
        # Maak een nieuw frame onder het bestaande connection frame
        self.auto_frame = ttk.LabelFrame(self.eco_gui.root, text="Automatische Modus", padding=10)
        
        # Plaats het frame onder het connection frame
        # We doen dit met de pack manager op index 1 (na het connection frame)
        self.auto_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Label voor status
        self.status_label = ttk.Label(self.auto_frame, text="Status: Uitgeschakeld", foreground="red")
        self.status_label.grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        
        # Frame voor knoppen
        button_frame = ttk.Frame(self.auto_frame)
        button_frame.grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)
        
        # Start knop
        self.start_button = ttk.Button(button_frame, text="Start Auto Mode", command=self.start_auto_mode)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        # Stop knop
        self.stop_button = ttk.Button(button_frame, text="Stop Auto Mode", command=self.stop_auto_mode, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Reset knop - voor het geval er iets vastloopt
        self.reset_button = ttk.Button(button_frame, text="Reset Liften", command=self.reset_lifts)
        self.reset_button.pack(side=tk.LEFT, padx=5)
        
        # Frame voor instellingen
        settings_frame = ttk.LabelFrame(self.auto_frame, text="Instellingen", padding=5)
        settings_frame.grid(row=2, column=0, columnspan=2, sticky=tk.W+tk.E, padx=5, pady=5)
        
        # Min wachttijd
        ttk.Label(settings_frame, text="Min wachttijd (sec):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.min_wait_var = tk.DoubleVar(value=self.min_wait_between_jobs)
        ttk.Spinbox(settings_frame, from_=0.5, to=10.0, increment=0.5, textvariable=self.min_wait_var, width=5).grid(row=0, column=1, padx=5, pady=2)
        
        # Max wachttijd
        ttk.Label(settings_frame, text="Max wachttijd (sec):").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.max_wait_var = tk.DoubleVar(value=self.max_wait_between_jobs)
        ttk.Spinbox(settings_frame, from_=1.0, to=20.0, increment=1.0, textvariable=self.max_wait_var, width=5).grid(row=0, column=3, padx=5, pady=2)
        
        # Error herstel tijd
        ttk.Label(settings_frame, text="Error herstel tijd (sec):").grid(row=0, column=4, sticky=tk.W, padx=5, pady=2)
        self.error_recovery_var = tk.DoubleVar(value=self.error_recovery_time)
        ttk.Spinbox(settings_frame, from_=1.0, to=10.0, increment=1.0, textvariable=self.error_recovery_var, width=5).grid(row=0, column=5, padx=5, pady=2)
    
    def start_auto_mode(self):
        """Start de automatische modus"""
        if not self.eco_gui.is_connected:
            self.show_status("Kan auto-modus niet starten: Niet verbonden met PLC", "red")
            return
        
        self.running = True
        self.show_status("Auto-modus actief", "green")
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        
        # Update de instellingen
        self.min_wait_between_jobs = self.min_wait_var.get()
        self.max_wait_between_jobs = self.max_wait_var.get()
        self.error_recovery_time = self.error_recovery_var.get()
        
        # Start de auto-modus asynchrone taak
        if self.auto_task:
            self.auto_task.cancel()
        self.auto_task = asyncio.create_task(self.auto_mode_loop())
        logger.info("Auto-modus gestart")
    
    def stop_auto_mode(self):
        """Stop de automatische modus"""
        self.running = False
        self.show_status("Auto-modus gestopt", "red")
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        
        if self.auto_task:
            self.auto_task.cancel()
            self.auto_task = None
        logger.info("Auto-modus gestopt")
    
    def reset_lifts(self):
        """Reset beide liften naar standaardposities"""
        if not self.eco_gui.is_connected:
            self.show_status("Kan liften niet resetten: Niet verbonden met PLC", "red")
            return
        
        logger.info("Bezig met resetten van beide liften...")
        
        # Forceer stoppen van auto-modus
        was_running = self.running
        if was_running:
            self.stop_auto_mode()
        
        # Reset beide liften door clear_task aan te roepen
        asyncio.create_task(self._reset_lifts_async())
        
        # Wacht eventjes en herstart auto-modus als die actief was
        if was_running:
            def restart_auto():
                logger.info("Auto-modus herstart na reset")
                self.start_auto_mode()
            
            # Start auto-modus opnieuw na 5 seconden
            self.eco_gui.root.after(5000, restart_auto)
    
    async def _reset_lifts_async(self):
        """Reset beide liften op een asynchrone manier"""
        # Reset Lift1
        await self.eco_gui.opcua_client.write_value(f"{LIFT1_ID}/Eco_iTaskType", 0)
        await asyncio.sleep(0.2)
        
        # Reset Lift2
        await self.eco_gui.opcua_client.write_value(f"{LIFT2_ID}/Eco_iTaskType", 0)
        await asyncio.sleep(0.2)
        
        # Clear eventuele errors
        await self.eco_gui.opcua_client.write_value(f"{LIFT1_ID}/xClearError", True)
        await asyncio.sleep(0.2)
        await self.eco_gui.opcua_client.write_value(f"{LIFT2_ID}/xClearError", True)
        
        logger.info("Beide liften gereset")
        self.show_status("Liften gereset", "blue")
        
        # Reset na 2 seconden weer terug naar normale status
        await asyncio.sleep(2.0)
        self.show_status("Auto-modus gestopt", "red")
    
    async def _check_and_acknowledge(self):
        """Controleer of er acknowledgements nodig zijn en voer ze uit"""
        for lift_id in LIFTS:
            try:
                # Check of GUI nog bestaat voordat we verdergaan
                if not self._is_gui_available():
                    return
                
                # Lees of er een acknowledgement nodig is
                ack_type = await self.eco_gui.opcua_client.read_value(f"{lift_id}/EcoAck_iAssingmentType")
                
                if ack_type and ack_type > 0:
                    # Er is een acknowledgement nodig, voer het uit
                    logger.info(f"Auto-acknowledge voor {lift_id}, type {ack_type}")
                    
                    # Wacht een willekeurige tijd tussen 0.5 en 1.5 seconden om menselijk gedrag na te bootsen
                    human_delay = random.uniform(0.5, 1.5)
                    await asyncio.sleep(human_delay)
                    
                    # Check nogmaals of GUI nog bestaat
                    if not self._is_gui_available():
                        return
                    
                    # Voer de acknowledge uit met de juiste VariantType (direct van asyncua importeren)
                    success = await self.eco_gui.opcua_client.write_value(
                        f"{lift_id}/EcoAck_xAcknowldeFromEco", 
                        True, 
                        ua.VariantType.Boolean
                    )
                    
                    if success:
                        logger.info(f"Acknowledgement gestuurd voor {lift_id}")
                    else:
                        logger.warning(f"Kon acknowledgement niet sturen voor {lift_id}")
            
            except Exception as e:
                logger.error(f"Fout bij controleren/acknowledgen voor {lift_id}: {e}")
    
    async def _check_and_clear_errors(self):
        """Controleer of er fouten zijn en reset ze"""
        for lift_id in LIFTS:
            try:
                # Check of GUI nog bestaat voordat we verdergaan
                if not self._is_gui_available():
                    return
                
                # Lees de foutcode
                error_code = await self.eco_gui.opcua_client.read_value(f"{lift_id}/iErrorCode")
                
                if error_code and error_code > 0:
                    # Er is een fout, wacht even en reset deze
                    logger.info(f"Fout gedetecteerd voor {lift_id}: Code {error_code}")
                    
                    # Wacht volgens de ingestelde error recovery tijd
                    await asyncio.sleep(self.error_recovery_time)
                    
                    # Check nogmaals of GUI nog bestaat
                    if not self._is_gui_available():
                        return
                    
                    # Reset de fout met de juiste VariantType
                    success = await self.eco_gui.opcua_client.write_value(
                        f"{lift_id}/xClearError", 
                        True, 
                        ua.VariantType.Boolean
                    )
                    
                    if success:
                        logger.info(f"Fout gereset voor {lift_id}")
                        # Markeer de lift als niet meer bezet na een fout
                        self.lift_busy[lift_id] = False
                    else:
                        logger.warning(f"Kon fout niet resetten voor {lift_id}")
            
            except Exception as e:
                logger.error(f"Fout bij controleren/resetten van fouten voor {lift_id}: {e}")
    
    async def _check_and_submit_jobs(self):
        """Controleer of er nieuwe jobs kunnen worden aangemaakt en verzonden"""
        for lift_id in LIFTS:
            try:
                # Check of GUI nog bestaat voordat we verdergaan
                if not self._is_gui_available():
                    return
                
                # Alleen een nieuwe job sturen als de lift niet bezet is
                if self.lift_busy[lift_id]:
                    continue
                
                current_time = time.time()
                time_since_last_job = current_time - self.last_job_time.get(lift_id, 0)
                
                # Controleer of er genoeg tijd is verstreken sinds de laatste job
                if time_since_last_job < self.min_wait_between_jobs:
                    continue
                
                # Lees de huidige cyclus van de lift
                cycle = await self.eco_gui.opcua_client.read_value(f"{lift_id}/iCycle")
                
                # Alleen een job sturen als de lift in de ready state is (cyclus 10)
                if cycle == 10:
                    # Kans om een nieuwe job te sturen, afhankelijk van verstreken tijd
                    max_wait = self.max_wait_between_jobs
                    chance = min(1.0, (time_since_last_job - self.min_wait_between_jobs) / (max_wait - self.min_wait_between_jobs))
                    
                    if random.random() < chance:
                        # Tijd om een nieuwe job te sturen!
                        await self._submit_random_job(lift_id)
                        self.last_job_time[lift_id] = current_time
                        self.lift_busy[lift_id] = True
            
            except Exception as e:
                logger.error(f"Fout bij controleren/verzenden van jobs voor {lift_id}: {e}")
    
    async def _submit_random_job(self, lift_id):
        """Maak een willekeurige job aan en verstuur deze"""
        try:
            # Check of GUI nog bestaat voordat we verdergaan
            if not self._is_gui_available():
                return False
            
            # Kies willekeurig een type job: 1=FullAssignment, 2=MoveTo, 3=PreparePickUp
            task_type = random.choices([1, 2, 3], weights=[0.95, 0.025, 0.025])[0]
            
            # Gebruik de juiste range voor deze lift om botsingen te voorkomen
            zone = self.lift_zones[lift_id]
            
            # Kies willekeurige origin en destination uit de toegewezen zones
            origin = random.randint(*zone['origin_range'])
            
            # Voor MoveTo (type 2) kan origin 0 zijn
            if task_type == 2 and random.random() < 0.3:
                origin = 0
            
            # Voor FullAssignment (type 1) hebben we origin en destination nodig
            if task_type == 1:
                destination = random.randint(*zone['dest_range'])
                # Zorg dat destination niet hetzelfde is als origin
                while destination == origin:
                    destination = random.randint(*zone['dest_range'])
            else:
                # Voor andere types kan destination 0 zijn of gelijk aan origin
                if random.random() < 0.5:
                    destination = 0
                else:
                    destination = origin
            
            logger.info(f"Stuur willekeurige job naar {lift_id}: Type={task_type}, Origin={origin}, Dest={destination}")
            
            # Gebruik de juiste VariantType voor integer waarden
            variant_type = ua.VariantType.Int16
            
            # Schrijf de waarden in de juiste volgorde
            await self.eco_gui.opcua_client.write_value(f"{lift_id}/Eco_iOrigination", origin, variant_type)
            await asyncio.sleep(0.1)
            await self.eco_gui.opcua_client.write_value(f"{lift_id}/Eco_iDestination", destination, variant_type)
            await asyncio.sleep(0.1)
            await self.eco_gui.opcua_client.write_value(f"{lift_id}/Eco_iTaskType", task_type, variant_type)
            
            # Markeer de tijd wanneer de job is verzonden
            self.last_job_time[lift_id] = time.time()
            
            return True
        except Exception as e:
            logger.error(f"Fout bij verzenden willekeurige job voor {lift_id}: {e}")
            return False
    
    def _is_gui_available(self):
        """Controleer of de GUI nog bestaat en gebruikt kan worden"""
        try:
            # Controleer of het root venster nog bestaat
            return self.eco_gui.root.winfo_exists()
        except:
            return False
            
    def show_status(self, message, color="black"):
        """Update de statuslabel als deze nog bestaat"""
        try:
            # Controleer of het statuslabel nog bestaat en bruikbaar is
            if self._is_gui_available() and hasattr(self, 'status_label'):
                self.status_label.config(text=f"Status: {message}", foreground=color)
        except Exception as e:
            # Stille fout-afhandeling bij GUI-operaties
            logger.debug(f"Kon status niet updaten: {e}")
            pass

    async def auto_mode_loop(self):
        """Hoofdlus voor de automatische modus"""
        try:
            while self.running:
                if not self.eco_gui.is_connected:
                    self.show_status("PLC verbinding verloren - Auto-modus gestopt", "red")
                    self.stop_auto_mode()
                    break
                
                # Kijk eerst of we moeten acknowledgen voor een lift
                await self._check_and_acknowledge()
                
                # Kijk of er errors zijn die gereset moeten worden
                await self._check_and_clear_errors()
                
                # Kijk of er een nieuwe job kan worden aangemaakt en gestuurd
                await self._check_and_submit_jobs()
                
                # Even pauze om CPU gebruik te beperken
                await asyncio.sleep(0.3)
        
        except asyncio.CancelledError:
            logger.info("Auto-mode loop geannuleerd")
        except Exception as e:
            logger.error(f"Fout in auto-mode loop: {e}", exc_info=True)
            self.show_status(f"Fout: {str(e)[:30]}...", "red")
            self.stop_auto_mode()
    
    def _check_job_complete(self, lift_id, cycle, comment):
        """Controleert of een job voltooid is op basis van cycle en comment"""
        # Jobs zijn voltooid bij cycle 10 (ready) of als er "Done" in de comment staat
        if cycle == 10:
            return True
        
        if comment and "Done" in comment:
            return True
        
        return False

# Functie om de Auto Mode Manager toe te voegen aan de EcoSystem GUI
def add_auto_mode_to_gui(ecosystem_gui):
    """Voegt de Auto Mode Manager toe aan de EcoSystem GUI"""
    auto_manager = AutoModeManager(ecosystem_gui)
    return auto_manager