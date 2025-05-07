import tkinter as tk
import logging
import queue
import time

logger = logging.getLogger(__name__)

# Visualization constants
CANVAS_HEIGHT = 600
CANVAS_WIDTH = 650
MAX_ROWS_LEFT = 50
MAX_ROWS_RIGHT = 49
MIN_ROW = -2
LIFT_WIDTH_VIS = 50 
LIFT_HEIGHT_RATIO = 0.013
LIFT1_COLOR = 'blue'
LIFT2_COLOR = 'green'
TRAY_COLOR = '#FFA500'
TOP_MARGIN = 50
BOTTOM_MARGIN = 50

LIFT1_ID = 'Lift1'
LIFT2_ID = 'Lift2'
LIFTS = (LIFT1_ID, LIFT2_ID)

class LiftVisualizationManager:
    def __init__(self, root, canvas, lift_ids):
        self.root = root
        self.canvas = canvas
        self.lift_ids = lift_ids

        self.lift_visuals = {}
        self.rack_info = {}
        self.current_animation_tasks = {lift_id: None for lift_id in lift_ids}
        self.last_position = {lift_id: 1 for lift_id in lift_ids}

        # Als een animatie in uitvoering is
        self.animation_running = {lift_id: False for lift_id in lift_ids}
        
        # We zullen deze keer GEEN aparte thread gebruiken, maar de Tkinter after-methode
        # Dit voorkomt de "main thread is not in main loop" fout

        self._setup_warehouse_visualization()

    def _setup_warehouse_visualization(self):
        # This method combines the logic from create_rack_visualization 
        # and the initial drawing of lifts, forks, trays, and labels.
        
        shaft_width = 80
        rack_width = 150
        center_x = CANVAS_WIDTH / 2

        # Usable height for racks
        usable_height = CANVAS_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN
        row_height_left = usable_height / MAX_ROWS_LEFT
        row_height_right = usable_height / MAX_ROWS_RIGHT

        # Left Rack
        left_rack_x1 = center_x - shaft_width/2 - 40 - rack_width
        left_rack_x2 = center_x - shaft_width/2 - 40
        self.canvas.create_rectangle(
            left_rack_x1, TOP_MARGIN, left_rack_x2, CANVAS_HEIGHT - BOTTOM_MARGIN,
            outline='black', width=1, fill='#E0E0E0'
        )

        # Lift Shaft
        shaft_x1 = center_x - shaft_width/2
        shaft_x2 = center_x + shaft_width/2
        self.canvas.create_rectangle(
            shaft_x1, 0, shaft_x2, CANVAS_HEIGHT,
            outline='black', width=2, fill='#F0F0F0'
        )

        # Right Rack
        right_rack_x1 = center_x + shaft_width/2 + 40
        right_rack_x2 = center_x + shaft_width/2 + 40 + rack_width
        self.canvas.create_rectangle(
            right_rack_x1, TOP_MARGIN, right_rack_x2, CANVAS_HEIGHT - BOTTOM_MARGIN,
            outline='black', width=1, fill='#E0E0E0'
        )

        grid_height_left = usable_height / MAX_ROWS_LEFT
        slot_height = grid_height_left * 0.7

        for row in range(1, MAX_ROWS_LEFT + 1):
            y_pos = CANVAS_HEIGHT - BOTTOM_MARGIN - (row * grid_height_left) + (grid_height_left/2)
            self.canvas.create_rectangle(
                left_rack_x1 + 5, y_pos - slot_height/2,
                left_rack_x2 - 5, y_pos + slot_height/2,
                outline='gray', width=1, fill='#8fbc8f', tags=f"rack_left_{row}"
            )
            if row == 1 or row % 5 == 0:
                self.canvas.create_text(left_rack_x1 - 10, y_pos, text=str(row), font=("Arial", 7), anchor="e")

        grid_height_right = usable_height / MAX_ROWS_RIGHT
        for i in range(MAX_ROWS_RIGHT):
            row = i + 51
            y_pos = CANVAS_HEIGHT - BOTTOM_MARGIN - (i * grid_height_right) - (grid_height_right/2)
            self.canvas.create_rectangle(
                right_rack_x1 + 5, y_pos - slot_height/2,
                right_rack_x2 - 5, y_pos + slot_height/2,
                outline='gray', width=1, fill='#8fbc8f', tags=f"rack_right_{row}"
            )
            if row == 51 or row % 5 == 0:
                 self.canvas.create_text(right_rack_x2 + 10, y_pos, text=str(row), font=("Arial", 7), anchor="w")

        # Service locations
        service_100_y = TOP_MARGIN / 2
        self.canvas.create_text(center_x, service_100_y - 15, text="Service 100", font=("Arial", 8))
        self.canvas.create_rectangle(center_x - 30, service_100_y - 5, center_x + 30, service_100_y + 5, outline='blue', width=1, fill='lightblue', tags="service_100")

        service_neg2_y = CANVAS_HEIGHT - BOTTOM_MARGIN / 2
        self.canvas.create_text(center_x, service_neg2_y - 15, text="Service -2", font=("Arial", 8))
        self.canvas.create_rectangle(center_x - 30, service_neg2_y - 5, center_x + 30, service_neg2_y + 5, outline='blue', width=1, fill='lightblue', tags="service_neg2")

        self.canvas.create_text(left_rack_x1 + rack_width/2, CANVAS_HEIGHT - 15, text="← Operator Side", font=("Arial", 8))
        self.canvas.create_text(right_rack_x1 + rack_width/2, CANVAS_HEIGHT - 15, text="Robot Side →", font=("Arial", 8))

        # Store rack_info
        self.rack_info = {
            'left': {'x1': left_rack_x1, 'x2': left_rack_x2, 'y_start': CANVAS_HEIGHT - BOTTOM_MARGIN, 'y_end': TOP_MARGIN, 'row_height': row_height_left, 'max_rows': MAX_ROWS_LEFT},
            'right': {'x1': right_rack_x1, 'x2': right_rack_x2, 'y_start': CANVAS_HEIGHT - BOTTOM_MARGIN, 'y_end': TOP_MARGIN, 'row_height': row_height_right, 'max_rows': MAX_ROWS_RIGHT},
            'service': {'100': {'y': service_100_y}, '-2': {'y': service_neg2_y}}
        }

        # Lift parameters
        lift_y_size = CANVAS_HEIGHT * LIFT_HEIGHT_RATIO
        lift_width_runtime = shaft_width * 0.8

        initial_lift_y_positions = [CANVAS_HEIGHT / 3, CANVAS_HEIGHT * 2 / 3] 

        for i, lift_id in enumerate(self.lift_ids):
            lift_color = LIFT1_COLOR if lift_id == LIFT1_ID else LIFT2_COLOR
            initial_y = initial_lift_y_positions[i % len(initial_lift_y_positions)]
            
            lift_rect = self.canvas.create_rectangle(
                center_x - lift_width_runtime/2, initial_y,
                center_x + lift_width_runtime/2, initial_y + lift_y_size,
                fill=lift_color, tags=(f"{lift_id}_lift",)
            )
            location_label = self.canvas.create_text(
                center_x, 10 + (i * 20), text=f"{lift_id}: Row 1",
                font=("Arial", 9, "bold"), fill=lift_color, tags=(f"{lift_id}_label",)
            )
            fork_width = 25
            fork_rect = self.canvas.create_rectangle(
                center_x - fork_width/2, initial_y + lift_y_size*0.1,
                center_x + fork_width/2, initial_y + lift_y_size*0.9,
                fill='gray', tags=(f"{lift_id}_fork",)
            )
            tray_width = 30
            tray_rect = self.canvas.create_rectangle(
                center_x - tray_width/2, initial_y + lift_y_size*0.15,
                center_x + tray_width/2, initial_y + lift_y_size*0.85,
                fill=TRAY_COLOR, outline='brown', width=2, tags=(f"{lift_id}_tray",), state=tk.HIDDEN
            )

            self.lift_visuals[lift_id] = {
                'rect': lift_rect, 'fork': fork_rect, 'tray': tray_rect,
                'lift_width': lift_width_runtime, 'fork_width': fork_width, 'tray_width': tray_width,
                'y_size': lift_y_size, 'shaft_center_x': center_x, 'shaft_width': shaft_width,
                'current_y': initial_y, 'target_y': initial_y, 'location_label': location_label,
                'color': lift_color
            }
            self.last_position[lift_id] = 1

    def _calculate_y_position(self, row):
        if not self.rack_info:
            logger.error("Rack info not initialized before calculating y position.")
            return CANVAS_HEIGHT / 2

        if row == -2: return self.rack_info['service']['-2']['y']
        if row == 100: return self.rack_info['service']['100']['y']
        
        side = 'left' if 1 <= row <= MAX_ROWS_LEFT else 'right' if 51 <= row <= (50 + MAX_ROWS_RIGHT) else None
        if not side:
            logger.warning(f"Invalid row {row} for y-position calculation. Defaulting.")
            return CANVAS_HEIGHT / 2

        rack = self.rack_info[side]
        row_index = (row - 1) if side == 'left' else (row - 51)
        position = rack['y_start'] - (row_index * rack['row_height']) - (rack['row_height'] / 2)
        return position

    def animate_lift_movement(self, lift_id, target_row):
        """Start a new animation to move the lift to a target row"""
        if lift_id not in self.lift_visuals:
            logger.warning(f"Attempted to animate non-existent lift: {lift_id}")
            return

        # Annuleer een eventuele bestaande animatie
        if self.current_animation_tasks.get(lift_id):
            self.root.after_cancel(self.current_animation_tasks[lift_id])
            self.current_animation_tasks[lift_id] = None
            self.animation_running[lift_id] = False
            logger.debug(f"Cancelled existing animation for lift {lift_id}")

        # Haal huidige visuele data op
        vis_data = self.lift_visuals[lift_id]
        current_logical_row = self.last_position.get(lift_id, 1)

        # Bereken huidige en doel Y posities
        current_center_y_canvas = self._calculate_y_position(current_logical_row) 
        target_center_y_canvas = self._calculate_y_position(target_row)

        # Als de lift al op de doelpositie is (of er bijna)
        if abs(current_center_y_canvas - target_center_y_canvas) < 1:
            if current_logical_row != target_row:
                self.last_position[lift_id] = target_row
                # Update de label om de nieuwe positie te tonen
                self.canvas.itemconfig(vis_data['location_label'], text=f"{lift_id}: Row {target_row}")
                logger.info(f"Lift {lift_id} already at target row {target_row} (visual check). Logical position updated.")
            return

        logger.info(f"Lift {lift_id} moving from row {current_logical_row} (Y: {current_center_y_canvas:.2f}) to {target_row} (Y: {target_center_y_canvas:.2f})")

        # Start de animatie met behulp van Tkinter's after-mechanisme
        # Dit is volledig thread-veilig omdat het op de hoofdthread draait
        self.animation_running[lift_id] = True
        
        # Parameters voor de animatie
        total_steps = 10  # Aantal stappen in de animatie
        duration_ms = 50  # Milliseconden tussen stappen (sneller = 50ms, langzamer = 100ms)
        
        # Start de eerste animatiestap
        self._animate_lift_step(
            lift_id=lift_id,
            start_y=current_center_y_canvas,
            target_y=target_center_y_canvas,
            target_row=target_row,
            current_step=0,
            total_steps=total_steps,
            step_duration_ms=duration_ms
        )

    def _animate_lift_step(self, lift_id, start_y, target_y, target_row, current_step, total_steps, step_duration_ms):
        """Voer één stap van de liftanimatie uit"""
        if lift_id not in self.lift_visuals or not self.animation_running[lift_id]:
            return
            
        # Bereken de Y-positie voor de huidige stap
        progress = (current_step + 1) / total_steps
        current_y = start_y + (target_y - start_y) * progress
        
        # Update de lift positie voor deze stap
        self._update_lift_position(lift_id, current_y)
        
        # Als dit de laatste stap is, markeer de animatie als voltooid
        if current_step >= total_steps - 1:
            self.last_position[lift_id] = target_row
            self.animation_running[lift_id] = False
            self.current_animation_tasks[lift_id] = None
            
            # Zorg voor een exacte laatste positie
            final_y = target_y
            self._update_lift_position(lift_id, final_y)
            
            # Update de locatie label
            vis_data = self.lift_visuals[lift_id]
            self.canvas.itemconfig(vis_data['location_label'], text=f"{lift_id}: Row {target_row}")
            
            logger.info(f"Lift {lift_id} animation completed at row {target_row}")
        else:
            # Anders plan de volgende animatiestap
            next_step = current_step + 1
            task_id = self.root.after(
                step_duration_ms,
                lambda: self._animate_lift_step(
                    lift_id, start_y, target_y, target_row, 
                    next_step, total_steps, step_duration_ms
                )
            )
            # Sla de task ID op zodat we deze later kunnen annuleren indien nodig
            self.current_animation_tasks[lift_id] = task_id

    def _update_lift_position(self, lift_id, center_y):
        """Helper method to update lift and associated elements at a specific Y position"""
        if lift_id not in self.lift_visuals:
            return
            
        vis_data = self.lift_visuals[lift_id]
        lift_rect = vis_data['rect']
        fork_rect = vis_data['fork']
        tray_rect = vis_data['tray']
        
        # Calculate top and bottom coordinates
        y_offset = vis_data['y_size'] / 2
        lift_y1 = center_y - y_offset
        lift_y2 = center_y + y_offset
        
        # Update lift position
        self.canvas.coords(lift_rect, 
                          vis_data['shaft_center_x'] - vis_data['lift_width']/2, lift_y1,
                          vis_data['shaft_center_x'] + vis_data['lift_width']/2, lift_y2)
        
        # Get fork position
        fork_tags = self.canvas.gettags(fork_rect)
        fork_side_val = 0  # middle
        if "side_right" in fork_tags: fork_side_val = 1
        elif "side_left" in fork_tags: fork_side_val = 2

        # Calculate fork x offset based on side
        fork_x_offset = 0
        if fork_side_val == 1:  # Right
            fork_x_offset = (vis_data['lift_width'] / 2) - (vis_data['fork_width'] / 2) - 2
        elif fork_side_val == 2:  # Left
            fork_x_offset = -((vis_data['lift_width'] / 2) - (vis_data['fork_width'] / 2) - 2)
        
        # Update fork position
        self.canvas.coords(fork_rect, 
                          vis_data['shaft_center_x'] + fork_x_offset - vis_data['fork_width']/2, lift_y1 + vis_data['y_size']*0.1,
                          vis_data['shaft_center_x'] + fork_x_offset + vis_data['fork_width']/2, lift_y1 + vis_data['y_size']*0.9)
        
        # Update tray position if visible
        if self.canvas.itemcget(tray_rect, 'state') == 'normal':
            tray_x_offset = fork_x_offset  # Tray follows fork
            self.canvas.coords(tray_rect,
                              vis_data['shaft_center_x'] + tray_x_offset - vis_data['tray_width']/2, lift_y1 + vis_data['y_size']*0.15,
                              vis_data['shaft_center_x'] + tray_x_offset + vis_data['tray_width']/2, lift_y1 + vis_data['y_size']*0.85)
        
        # Update stored current position
        vis_data['current_y'] = lift_y1

    def animate_tray_action(self, lift_id, action_type, row, fork_side_val): 
        # This is a simplified version that only changes visibility
        if lift_id not in self.lift_visuals: return
        vis = self.lift_visuals[lift_id]
        tray_rect = vis['tray']
        
        tray_visible = action_type == 'pickup'
        self.canvas.itemconfig(tray_rect, state=tk.NORMAL if tray_visible else tk.HIDDEN)
        logger.debug(f"Lift {lift_id} tray action: {action_type} at row {row}. Tray visible: {tray_visible}")
        
        # Update visual state to show the correct fork position and tray visibility
        self.update_lift_visual_state(lift_id, self.last_position[lift_id], tray_visible, fork_side_val, False)

    def update_lift_visual_state(self, lift_id, current_row, has_tray, fork_side_val, is_error):
        # fork_side_val: 0=middle, 1=robot side (right), 2=operator side (left)
        if lift_id not in self.lift_visuals: return

        vis_data = self.lift_visuals[lift_id]
        lift_rect = vis_data['rect']
        fork_rect = vis_data['fork']
        tray_rect = vis_data['tray']
        location_label = vis_data['location_label']

        # Update lift color based on error state
        base_color = vis_data['color']
        current_lift_color = 'red' if is_error else base_color
        if self.canvas.itemcget(lift_rect, 'fill') != current_lift_color:
            self.canvas.itemconfig(lift_rect, fill=current_lift_color)

        # Update tray visibility
        new_tray_state = tk.NORMAL if has_tray else tk.HIDDEN
        if self.canvas.itemcget(tray_rect, 'state') != new_tray_state:
            self.canvas.itemconfig(tray_rect, state=new_tray_state)

        # Update fork side (lateral movement) and tags
        fork_x_offset = 0
        current_fork_tags = self.canvas.gettags(fork_rect)
        new_fork_tags = [tag for tag in current_fork_tags if not tag.startswith("side_")]

        # Verhoogde offset om vorken verder in de stelling te laten gaan
        fork_extension_factor = 8.0  # Verhoogd naar 8.0 voor betere visualisatie in de stelling
        
        if fork_side_val == 1: # Right (Robot)
            fork_x_offset = (vis_data['lift_width'] / 2) + (vis_data['fork_width'] / 2) * fork_extension_factor
            new_fork_tags.append("side_right")
        elif fork_side_val == 2: # Left (Operator)
            fork_x_offset = -((vis_data['lift_width'] / 2) + (vis_data['fork_width'] / 2) * fork_extension_factor)
            new_fork_tags.append("side_left")
        else: # Middle
            new_fork_tags.append("side_middle") # Default or 0
        
        self.canvas.itemconfig(fork_rect, tags=tuple(new_fork_tags))

        # Gebruik de opgeslagen current_y tijdens animaties
        if not self.animation_running.get(lift_id, False):
            lift_center_y = self._calculate_y_position(current_row)
            vis_data['current_y'] = lift_center_y - vis_data['y_size']/2
        
        lift_y1 = vis_data['current_y']

        # Update fork position
        self.canvas.coords(fork_rect, 
                           vis_data['shaft_center_x'] + fork_x_offset - vis_data['fork_width']/2, 
                           lift_y1 + vis_data['y_size']*0.1,
                           vis_data['shaft_center_x'] + fork_x_offset + vis_data['fork_width']/2, 
                           lift_y1 + vis_data['y_size']*0.9)

        # Update tray position if visible
        if has_tray:
            tray_x_offset = fork_x_offset # Tray follows fork
            self.canvas.coords(tray_rect,
                               vis_data['shaft_center_x'] + tray_x_offset - vis_data['tray_width']/2, 
                               lift_y1 + vis_data['y_size']*0.15,
                               vis_data['shaft_center_x'] + tray_x_offset + vis_data['tray_width']/2, 
                               lift_y1 + vis_data['y_size']*0.85)

        # Update Location Label
        self.canvas.itemconfig(location_label, text=f"{lift_id}: Row {current_row}")

        # Animate lift movement if logical row has changed and not already animating to it
        if current_row != self.last_position.get(lift_id) and current_row != MIN_ROW:
            # Alleen een nieuwe animatie starten als er geen loopt
            if not self.animation_running.get(lift_id, False):
                self.animate_lift_movement(lift_id, current_row)
        elif current_row == self.last_position.get(lift_id):
            # Als positie hetzelfde is, zorg dat de lift op de juiste berekende Y-positie staat
            if not self.animation_running.get(lift_id, False):
                target_y_center = self._calculate_y_position(current_row)
                y_offset = vis_data['y_size'] / 2
                expected_y1 = target_y_center - y_offset
                current_coords = self.canvas.coords(lift_rect)
                if abs(current_coords[1] - expected_y1) > 0.1:
                    self.canvas.coords(lift_rect, 
                                   vis_data['shaft_center_x'] - vis_data['lift_width']/2, expected_y1,
                                   vis_data['shaft_center_x'] + vis_data['lift_width']/2, expected_y1 + vis_data['y_size'])
                    vis_data['current_y'] = expected_y1
                    # Also update fork and tray based on this corrected lift_y1
                    self.canvas.coords(fork_rect, 
                               vis_data['shaft_center_x'] + fork_x_offset - vis_data['fork_width']/2, 
                               expected_y1 + vis_data['y_size']*0.1,
                               vis_data['shaft_center_x'] + fork_x_offset + vis_data['fork_width']/2, 
                               expected_y1 + vis_data['y_size']*0.9)
                    if has_tray:
                        self.canvas.coords(tray_rect,
                                   vis_data['shaft_center_x'] + fork_x_offset - vis_data['tray_width']/2, 
                                   expected_y1 + vis_data['y_size']*0.15,
                                   vis_data['shaft_center_x'] + fork_x_offset + vis_data['tray_width']/2, 
                                   expected_y1 + vis_data['y_size']*0.85)

    def _calculate_logical_row(self, y_position):
        """Determine the logical row based on the lift's Y position"""
        # Check service positions first
        for special_row, pos_data in self.rack_info['service'].items():
            if abs(y_position - pos_data['y']) < 20:  # Reasonable proximity
                return int(special_row)  # Convert '-2' to -2
                
        # Check left rack positions
        for row in range(1, MAX_ROWS_LEFT + 1):
            row_center_y = self._calculate_y_position(row)
            if abs(y_position - row_center_y) < 10:  # Reasonable proximity threshold
                return row
                
        # Check right rack positions
        for row in range(51, 51 + MAX_ROWS_RIGHT):
            row_center_y = self._calculate_y_position(row)
            if abs(y_position - row_center_y) < 10:
                return row
                
        # Default to row 1 if no match found
        return 1
