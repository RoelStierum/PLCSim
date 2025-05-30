import tkinter as tk
import logging
import queue
import time

logger = logging.getLogger(__name__)

# Visualization constants
CANVAS_HEIGHT = 600
CANVAS_WIDTH = 600  # Increased from 650 to 800 for wider canvas
MAX_ROWS_LEFT = 50
MAX_ROWS_RIGHT = 49
MIN_ROW = -2 # Used for logical row calculations, can be a service point
SERVICE_ROW_TOP = 100 # Logical row number for top service point
SERVICE_ROW_BOTTOM = -2 # Logical row number for bottom service point

LIFT_WIDTH_VIS = 50
LIFT_HEIGHT_RATIO = 0.013
LIFT1_COLOR = 'blue'
LIFT2_COLOR = 'green'
TRAY_COLOR = '#FFA500'
TOP_MARGIN = 70 # Adjusted from 50
BOTTOM_MARGIN = 50

LIFT1_ID = 'Lift1'
LIFT2_ID = 'Lift2'
LIFTS = (LIFT1_ID, LIFT2_ID)

# Fork Side Constants (mirroring PLCSim.py for clarity in this module)
MiddenLocation = 0
OpperatorSide = 1  # Left (operator side)
RobotSide = 2      # Right (robot side)

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
        
        self._setup_warehouse_visualization()

    def _setup_warehouse_visualization(self):
        # This method combines the logic from create_rack_visualization
        # and the initial drawing of lifts, forks, trays, and labels.

        shaft_width = 80
        rack_width = 150
        center_x = CANVAS_WIDTH / 2 + 5  

        # Usable height for racks
        usable_height = CANVAS_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN
        row_height_left = usable_height / MAX_ROWS_LEFT
        row_height_right = usable_height / MAX_ROWS_RIGHT

        # --- Define Zone Colors ---
        operator_zone_color = "#FFDDC1" # Light orange/peach for Operator Zone
        robot_zone_color = "#D1E8FF"    # Light blue for Robot Zone
        shaft_color = "#D8D8D8"
        service_area_color = "#8989FF" # Light purple/lavender for service areas

        # --- Draw Zones ---
        # Operator Zone (Left)
        left_rack_x1 = center_x - shaft_width/2 - 40 - rack_width
        left_rack_x2 = center_x - shaft_width/2 - 40
        self.canvas.create_rectangle(
            left_rack_x1, TOP_MARGIN, left_rack_x2, CANVAS_HEIGHT - BOTTOM_MARGIN,
            outline='black', width=1, fill=operator_zone_color, tags="operator_zone_bg"
        )
        self.canvas.create_text(left_rack_x1 + rack_width/2, TOP_MARGIN - 15, text="Operator Zone (Rows 1-50)", font=("Arial", 11, "bold"))

        # Robot Zone (Right)
        right_rack_x1 = center_x + shaft_width/2 + 40
        right_rack_x2 = center_x + shaft_width/2 + 40 + rack_width
        self.canvas.create_rectangle(
            right_rack_x1, TOP_MARGIN, right_rack_x2, CANVAS_HEIGHT - BOTTOM_MARGIN,
            outline='black', width=1, fill=robot_zone_color, tags="robot_zone_bg"
        )
        self.canvas.create_text(right_rack_x1 + rack_width/2, TOP_MARGIN - 15, text="Robot Zone (Rows 51-99)", font=("Arial", 11, "bold"))

        # Lift Shaft
        shaft_x1 = center_x - shaft_width/2
        shaft_x2 = center_x + shaft_width/2
        self.canvas.create_rectangle(
            shaft_x1, 0, shaft_x2, CANVAS_HEIGHT,
            outline='black', width=2, fill=shaft_color
        )

        # --- Draw Rack Slots (over the zones) ---
        grid_height_left = usable_height / MAX_ROWS_LEFT
        slot_height = grid_height_left * 0.7

        for row in range(1, MAX_ROWS_LEFT + 1):
            y_pos = CANVAS_HEIGHT - BOTTOM_MARGIN - (row * grid_height_left) + (grid_height_left/2)
            self.canvas.create_rectangle(
                left_rack_x1 + 5, y_pos - slot_height/2,
                left_rack_x2 - 5, y_pos + slot_height/2,
                outline='gray', width=1, fill='#C8E6C8', tags=f"rack_left_{row}"
            )
            if row == 1 or row % 5 == 0:
                self.canvas.create_text(left_rack_x1 - 10, y_pos, text=str(row), font=("Arial", 11, "bold"), anchor="e")
        grid_height_right = usable_height / MAX_ROWS_RIGHT
        for i in range(MAX_ROWS_RIGHT):
            row = i + 51
            y_pos = CANVAS_HEIGHT - BOTTOM_MARGIN - (i * grid_height_right) - (grid_height_right / 2)
            self.canvas.create_rectangle(
                right_rack_x1 + 5, y_pos - slot_height/2,
                right_rack_x2 - 5, y_pos + slot_height/2,
                outline='gray', width=1, fill='#C8E6C8', tags=f"rack_right_{row}"
            )
            if row == 51 or row % 5 == 0 or row == (50 + MAX_ROWS_RIGHT):
                self.canvas.create_text(right_rack_x2 + 10, y_pos, text=str(row), font=("Arial", 11, "bold"), anchor="w")

        # --- Service Locations Visualization ---
        service_area_height = TOP_MARGIN * 0.8
        service_100_y_center = TOP_MARGIN / 2
        self.canvas.create_rectangle(shaft_x1, service_100_y_center - service_area_height/2,
                                     shaft_x2, service_100_y_center + service_area_height/2,
                                     fill=service_area_color, outline='darkblue', width=1, tags="service_100_bg")
        self.canvas.create_text(center_x, service_100_y_center, text=f"Service {SERVICE_ROW_TOP}", font=("Arial", 11), fill="darkblue", anchor=tk.CENTER)

        service_neg2_y_center = CANVAS_HEIGHT - (BOTTOM_MARGIN / 2)
        self.canvas.create_rectangle(shaft_x1, service_neg2_y_center - service_area_height/2,
                                     shaft_x2, service_neg2_y_center + service_area_height/2,
                                     fill=service_area_color, outline='darkblue', width=1, tags="service_-2_bg")
        self.canvas.create_text(center_x, service_neg2_y_center, text=f"Service {SERVICE_ROW_BOTTOM}", font=("Arial", 11), fill="darkblue", anchor=tk.CENTER)

        # Store rack_info (ensure y-positions for service areas are center points for lift calculations)
        self.rack_info = {
            'left': {'x1': left_rack_x1, 'x2': left_rack_x2, 'y_start_canvas': CANVAS_HEIGHT - BOTTOM_MARGIN, 'y_end_canvas': TOP_MARGIN, 'row_height_canvas': row_height_left, 'max_rows': MAX_ROWS_LEFT},
            'right': {'x1': right_rack_x1, 'x2': right_rack_x2, 'y_start_canvas': CANVAS_HEIGHT - BOTTOM_MARGIN, 'y_end_canvas': TOP_MARGIN, 'row_height_canvas': row_height_right, 'max_rows': MAX_ROWS_RIGHT},
            'service': {
                str(SERVICE_ROW_TOP): {'y_center_canvas': service_100_y_center},
                str(SERVICE_ROW_BOTTOM): {'y_center_canvas': service_neg2_y_center}
            }
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

            fork_width = 35
            fork_rect = self.canvas.create_rectangle(
                center_x - fork_width/2, initial_y + lift_y_size*0.1,
                center_x + fork_width/2, initial_y + lift_y_size*0.9,
                fill='gray', tags=(f"{lift_id}_fork",)
            )
            tray_width = 40
            tray_rect = self.canvas.create_rectangle(
                center_x - tray_width/2, initial_y + lift_y_size*0.15,
                center_x + tray_width/2, initial_y + lift_y_size*0.85,
                fill=TRAY_COLOR, outline='brown', width=2, tags=(f"{lift_id}_tray",), state=tk.HIDDEN
            )

            self.lift_visuals[lift_id] = {
                'rect': lift_rect, 'fork': fork_rect, 'tray': tray_rect,
                'lift_width': lift_width_runtime, 'fork_width': fork_width, 'tray_width': tray_width,
                'y_size': lift_y_size, 'shaft_center_x': center_x, 'shaft_width': shaft_width,
                'current_y': initial_y, 'target_y': initial_y,
                # 'location_label': location_label, # Removed as text is empty and stoplights are separate
                'color': lift_color
            }
            self.last_position[lift_id] = 1

    def _calculate_y_position(self, row):
        if not self.rack_info:
            logger.error("Rack info not initialized before calculating y position.")
            return CANVAS_HEIGHT / 2 # Default to center if not initialized

        # Handle service locations first using their stored y_center_canvas
        if row == SERVICE_ROW_BOTTOM: 
            return self.rack_info['service'][str(SERVICE_ROW_BOTTOM)]['y_center_canvas']
        if row == SERVICE_ROW_TOP: 
            return self.rack_info['service'][str(SERVICE_ROW_TOP)]['y_center_canvas']
        
        # Determine side and calculate position for regular rack rows
        side = None
        row_index_on_side = 0

        if 1 <= row <= MAX_ROWS_LEFT: # Operator side (left)
            side = 'left'
            row_index_on_side = row - 1 # 0-indexed for calculation from bottom of rack area
        elif (MAX_ROWS_LEFT + 1) <= row <= (MAX_ROWS_LEFT + MAX_ROWS_RIGHT): # Robot side (right)
            # Assuming rows 51-99 map to MAX_ROWS_LEFT + 1 to MAX_ROWS_LEFT + MAX_ROWS_RIGHT
            # Example: If MAX_ROWS_LEFT is 50, then row 51 is the 0th index on the right side.
            side = 'right'
            row_index_on_side = row - (MAX_ROWS_LEFT + 1) # 0-indexed for calculation from bottom of rack area
        else:
            logger.warning(f"Invalid row {row} for y-position calculation. Defaulting to center of canvas.")
            return CANVAS_HEIGHT / 2

        rack = self.rack_info[side]
        # y_start_canvas is the bottom of the rack drawing area (higher y-value)
        # We subtract because row 1 (or 51) is at the bottom of the visual rack, and higher rows go up (lower y-value)
        # The row_index_on_side increases as we go up the rack.
        position = rack['y_start_canvas'] - (row_index_on_side * rack['row_height_canvas']) - (rack['row_height_canvas'] / 2)
        return position

    def animate_lift_movement(self, lift_id, target_row):
        """Start a new animation to move the lift to a target row (time-based, smooth and consistent)"""
        if lift_id not in self.lift_visuals:
            logger.warning(f"Attempted to animate non-existent lift: {lift_id}")
            return

        # Annuleer een eventuele bestaande animatie
        if self.current_animation_tasks.get(lift_id):
            self.root.after_cancel(self.current_animation_tasks[lift_id])
            self.current_animation_tasks[lift_id] = None
            self.animation_running[lift_id] = False
            logger.debug(f"Cancelled existing animation for lift {lift_id}")

        vis_data = self.lift_visuals[lift_id]
        current_logical_row = self.last_position.get(lift_id, 1)
        current_center_y_canvas = self._calculate_y_position(current_logical_row)
        target_center_y_canvas = self._calculate_y_position(target_row)

        if abs(current_center_y_canvas - target_center_y_canvas) < 1:
            if current_logical_row != target_row:
                self.last_position[lift_id] = target_row
            return

        self.animation_running[lift_id] = True

        # Tijd-gebaseerde animatie: altijd vaste totale duur, ongeacht event loop delays
        total_rows = abs(target_row - current_logical_row)
        if total_rows == 0:
            logger.warning(f"Lift {lift_id} is already at the target row {target_row}. No animation needed.")
            self.animation_running[lift_id] = False
            return
        total_duration_ms = max(60, total_rows * 35)  # 60ms minimaal, 35ms per rij 
        start_time = time.perf_counter()
        end_time = start_time + (total_duration_ms / 2000.0)

        def step():
            now = time.perf_counter()
            t = min(1.0, (now - start_time) / (end_time - start_time))
            current_y = current_center_y_canvas + (target_center_y_canvas - current_center_y_canvas) * t
            self._update_lift_position(lift_id, current_y)
            if t >= 1.0:
                self.last_position[lift_id] = target_row
                self.animation_running[lift_id] = False
                self.current_animation_tasks[lift_id] = None
                self._update_lift_position(lift_id, target_center_y_canvas)
            else:
                self.current_animation_tasks[lift_id] = self.root.after(8, step)  # 8ms = ~120fps

        step()

    def _animate_lift_step(self, lift_id, start_y, target_y, target_row, current_step, total_steps, step_duration_ms):
        """Voer één stap van de liftanimatie uit (restored 'perfect' version)"""
        if lift_id not in self.lift_visuals or not self.animation_running[lift_id]:
            return
        progress = (current_step + 1) / total_steps
        current_y = start_y + (target_y - start_y) * progress
        self._update_lift_position(lift_id, current_y)
        if current_step >= total_steps - 1:
            self.last_position[lift_id] = target_row
            self.animation_running[lift_id] = False
            self.current_animation_tasks[lift_id] = None
            final_y = target_y
            self._update_lift_position(lift_id, final_y)
        else:
            next_step = current_step + 1
            task_id = self.root.after(
                step_duration_ms,
                lambda: self._animate_lift_step(
                    lift_id, start_y, target_y, target_row, 
                    next_step, total_steps, step_duration_ms
                )
            )
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

    def update_lift_visual_state(self, lift_id, current_row, has_tray, fork_side_from_plc, is_error): 
        if self.canvas is None:
            logger.error("Canvas not initialized in LiftVisualizationManager.")
            return

        # Fork mag alleen bewegen als de animatie klaar is en de lift op zijn bestemming staat
        if not hasattr(self, 'fork_move_allowed'):
            self.fork_move_allowed = {}
        self.fork_move_allowed[lift_id] = (not self.animation_running.get(lift_id, False)) and (current_row == self.last_position.get(lift_id))

        # Calculate y-coordinate based on current_row (for vertical lift positioning)
        y_pos = self._calculate_y_position(current_row)


        visual_fork_orientation = MiddenLocation # Default to Midden
        if fork_side_from_plc == 1:  # PLC RobotSide (physical right) correspondeert met visueel links
            visual_fork_orientation = RobotSide   # Visual RobotSide (forks to visual left)
        elif fork_side_from_plc == 2:  # PLC OpperatorSide (physical left) correspondeert met visueel rechts
            visual_fork_orientation = OpperatorSide # Visual OpperatorSide (forks to visual right)
        elif fork_side_from_plc == 0:  # PLC Midden
            visual_fork_orientation = MiddenLocation
        else:
            logger.warning(f"Lift {lift_id}: Unknown fork_side_from_plc value: {fork_side_from_plc}. Defaulting to Midden.")
            # visual_fork_orientation remains MiddenLocation due to initialization

        if lift_id not in self.lift_visuals: return

        vis_data = self.lift_visuals[lift_id]
        lift_rect = vis_data['rect']
        fork_rect = vis_data['fork']
        tray_rect = vis_data['tray']

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

        fork_extension_factor = 8.0  # Define fork_extension_factor before use
        
        # Gebruik visual_fork_orientation (afgeleid van PLC) om de vorkpositie te bepalen
        if visual_fork_orientation == OpperatorSide: # OpperatorSide (waarde 2) is visueel naar rechts
            fork_x_offset = (vis_data['lift_width'] / 2) + (vis_data['fork_width'] / 2) * fork_extension_factor
            new_fork_tags.append("side_right")
        elif visual_fork_orientation == RobotSide: # RobotSide (waarde 1) is visueel naar links
            fork_x_offset = -((vis_data['lift_width'] / 2) + (vis_data['fork_width'] / 2) * fork_extension_factor)
            new_fork_tags.append("side_left")
        else: # MiddenLocation (waarde 0) or default
            new_fork_tags.append("side_middle")
        
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


        # Animate lift movement if logical row has changed
        if current_row != self.last_position.get(lift_id):
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
        if abs(y_position - self.rack_info['service'][str(SERVICE_ROW_TOP)]['y_center_canvas']) < 20:  # Proximity to top service
            return SERVICE_ROW_TOP
        if abs(y_position - self.rack_info['service'][str(SERVICE_ROW_BOTTOM)]['y_center_canvas']) < 20: # Proximity to bottom service
            return SERVICE_ROW_BOTTOM
                
        # Check left rack positions (Operator Zone)
        rack_left = self.rack_info['left']
        # Iterate from logical row 1 to MAX_ROWS_LEFT
        for i in range(MAX_ROWS_LEFT):
            logical_row = i + 1
            # Calculate center y of this logical row on the canvas
            # y_start_canvas is bottom of rack, so subtract to go upwards
            row_center_y_canvas = rack_left['y_start_canvas'] - (i * rack_left['row_height_canvas']) - (rack_left['row_height_canvas'] / 2)
            if abs(y_position - row_center_y_canvas) < (rack_left['row_height_canvas'] / 2):
                return logical_row
                
        # Check right rack positions (Robot Zone)
        rack_right = self.rack_info['right']
        # Iterate for MAX_ROWS_RIGHT
        for i in range(MAX_ROWS_RIGHT):
            logical_row = MAX_ROWS_LEFT + 1 + i # e.g., 50 + 1 + 0 = 51 for the first row on the right
            row_center_y_canvas = rack_right['y_start_canvas'] - (i * rack_right['row_height_canvas']) - (rack_right['row_height_canvas'] / 2)
            if abs(y_position - row_center_y_canvas) < (rack_right['row_height_canvas'] / 2):
                return logical_row
                
        logger.warning(f"Could not determine logical row for Y={y_position:.2f}. Defaulting to {MIN_ROW}.")
        return MIN_ROW # Default if no specific match
