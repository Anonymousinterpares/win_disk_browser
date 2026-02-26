"""
Visualization Module for DiskInsight Pro
This module will be integrated in Phase 2 for advanced visualizations
"""

import tkinter as tk
from tkinter import Canvas
import math
from typing import List, Tuple, Optional
import colorsys

class TreemapVisualizer:
    """Creates treemap visualization of disk usage"""
    
    def __init__(self, canvas: Canvas):
        self.canvas = canvas
        self.color_map = {}
        self.rectangles = []
        self.tooltips = {}
        
    def generate_colors(self, count: int) -> List[str]:
        """Generate distinct colors for visualization"""
        colors = []
        for i in range(count):
            hue = i / count
            # Use high saturation and medium lightness for vibrant colors
            rgb = colorsys.hsv_to_rgb(hue, 0.7, 0.8)
            color = '#{:02x}{:02x}{:02x}'.format(
                int(rgb[0] * 255),
                int(rgb[1] * 255),
                int(rgb[2] * 255)
            )
            colors.append(color)
        return colors
        
    def calculate_treemap_rectangles(self, items: List[dict], x: int, y: int, 
                                    width: int, height: int) -> List[dict]:
        """Calculate rectangle positions for treemap using squarified algorithm"""
        if not items or width <= 0 or height <= 0:
            return []
            
        # Normalize sizes
        total_size = sum(item['size'] for item in items)
        if total_size == 0:
            return []
            
        rectangles = []
        
        # Simple slice and dice algorithm for now
        if width > height:
            # Slice vertically
            current_x = x
            for item in items:
                item_width = int(width * (item['size'] / total_size))
                if item_width > 0:
                    rect = {
                        'x': current_x,
                        'y': y,
                        'width': item_width,
                        'height': height,
                        'item': item
                    }
                    rectangles.append(rect)
                    current_x += item_width
        else:
            # Slice horizontally
            current_y = y
            for item in items:
                item_height = int(height * (item['size'] / total_size))
                if item_height > 0:
                    rect = {
                        'x': x,
                        'y': current_y,
                        'width': width,
                        'height': item_height,
                        'item': item
                    }
                    rectangles.append(rect)
                    current_y += item_height
                    
        return rectangles
        
    def draw_treemap(self, node_data: dict, max_depth: int = 3):
        """Draw treemap visualization of the file system"""
        self.canvas.delete("all")
        self.rectangles = []
        
        # Get canvas dimensions
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        
        if width <= 1 or height <= 1:
            # Canvas not ready yet
            self.canvas.after(100, lambda: self.draw_treemap(node_data, max_depth))
            return
            
        # Prepare data
        items = self.prepare_treemap_data(node_data, max_depth)
        
        # Generate colors
        colors = self.generate_colors(len(items))
        
        # Calculate rectangles
        rectangles = self.calculate_treemap_rectangles(
            items, 0, 0, width, height
        )
        
        # Draw rectangles
        for i, rect in enumerate(rectangles):
            color = colors[i % len(colors)]
            self.draw_rectangle(rect, color)
            
    def prepare_treemap_data(self, node: dict, max_depth: int, 
                            current_depth: int = 0) -> List[dict]:
        """Prepare data for treemap visualization"""
        items = []
        
        if current_depth >= max_depth:
            return [{
                'name': node.get('name', 'Unknown'),
                'size': node.get('size', 0),
                'path': node.get('path', ''),
                'is_dir': node.get('is_dir', False)
            }]
            
        if node.get('is_dir') and 'children' in node:
            for child in node['children']:
                child_items = self.prepare_treemap_data(
                    child, max_depth, current_depth + 1
                )
                items.extend(child_items)
        else:
            items.append({
                'name': node.get('name', 'Unknown'),
                'size': node.get('size', 0),
                'path': node.get('path', ''),
                'is_dir': node.get('is_dir', False)
            })
            
        return items
        
    def draw_rectangle(self, rect: dict, color: str):
        """Draw a single rectangle in the treemap"""
        x, y = rect['x'], rect['y']
        width, height = rect['width'], rect['height']
        item = rect['item']
        
        # Draw rectangle
        rect_id = self.canvas.create_rectangle(
            x, y, x + width, y + height,
            fill=color,
            outline="white",
            width=1
        )
        
        # Add text label if space permits
        if width > 50 and height > 30:
            text = item['name']
            if len(text) > 15:
                text = text[:12] + "..."
                
            text_id = self.canvas.create_text(
                x + width // 2,
                y + height // 2,
                text=text,
                fill="white",
                font=("Arial", 10, "bold"),
                width=width - 10
            )
            
        # Store rectangle info for interactivity
        self.rectangles.append({
            'id': rect_id,
            'item': item,
            'bounds': (x, y, x + width, y + height)
        })
        
        # Bind hover events
        self.canvas.tag_bind(rect_id, "<Enter>", 
                           lambda e: self.on_hover(rect_id, item))
        self.canvas.tag_bind(rect_id, "<Leave>", 
                           lambda e: self.on_leave(rect_id))
        
    def on_hover(self, rect_id: int, item: dict):
        """Handle mouse hover over rectangle"""
        # Highlight rectangle
        self.canvas.itemconfig(rect_id, outline="yellow", width=2)
        
        # Could show tooltip here in future
        
    def on_leave(self, rect_id: int):
        """Handle mouse leave from rectangle"""
        # Reset rectangle appearance
        self.canvas.itemconfig(rect_id, outline="white", width=1)


class SunburstVisualizer:
    """Creates sunburst (radial) visualization of disk usage"""
    
    def __init__(self, canvas: Canvas):
        self.canvas = canvas
        self.center_x = 0
        self.center_y = 0
        self.max_radius = 0
        self.segments = []
        
    def draw_sunburst(self, node_data: dict, max_depth: int = 4):
        """Draw sunburst visualization"""
        self.canvas.delete("all")
        self.segments = []
        
        # Get canvas dimensions
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        
        if width <= 1 or height <= 1:
            self.canvas.after(100, lambda: self.draw_sunburst(node_data, max_depth))
            return
            
        # Calculate center and radius
        self.center_x = width // 2
        self.center_y = height // 2
        self.max_radius = min(width, height) // 2 - 10
        
        # Draw from center outward
        self.draw_ring(node_data, 0, 0, 360, max_depth)
        
    def draw_ring(self, node: dict, inner_radius: int, start_angle: float, 
                  end_angle: float, max_depth: int, current_depth: int = 0):
        """Draw a ring segment of the sunburst"""
        if current_depth >= max_depth:
            return
            
        # Calculate ring thickness
        ring_thickness = self.max_radius // max_depth
        outer_radius = inner_radius + ring_thickness
        
        # Generate color based on depth and angle
        hue = (start_angle % 360) / 360
        saturation = 0.7 - (current_depth * 0.15)
        value = 0.8
        rgb = colorsys.hsv_to_rgb(hue, saturation, value)
        color = '#{:02x}{:02x}{:02x}'.format(
            int(rgb[0] * 255),
            int(rgb[1] * 255),
            int(rgb[2] * 255)
        )
        
        # Draw arc
        if inner_radius > 0:  # Skip center circle for root
            self.draw_arc(inner_radius, outer_radius, start_angle, 
                         end_angle, color, node)
            
        # Draw children
        if node.get('is_dir') and 'children' in node:
            children = node['children']
            total_size = sum(child.get('size', 0) for child in children)
            
            if total_size > 0:
                current_angle = start_angle
                angle_range = end_angle - start_angle
                
                for child in children:
                    child_size = child.get('size', 0)
                    child_angle = angle_range * (child_size / total_size)
                    
                    if child_angle > 0.5:  # Skip very small segments
                        self.draw_ring(
                            child,
                            outer_radius,
                            current_angle,
                            current_angle + child_angle,
                            max_depth,
                            current_depth + 1
                        )
                    
                    current_angle += child_angle
                    
    def draw_arc(self, inner_radius: int, outer_radius: int, 
                 start_angle: float, end_angle: float, color: str, node: dict):
        """Draw an arc segment"""
        # Convert angles to radians
        start_rad = math.radians(start_angle - 90)  # Start from top
        end_rad = math.radians(end_angle - 90)
        
        # Calculate points for polygon approximation
        points = []
        steps = max(int((end_angle - start_angle) / 5), 3)
        
        # Outer arc
        for i in range(steps + 1):
            angle = start_rad + (end_rad - start_rad) * i / steps
            x = self.center_x + outer_radius * math.cos(angle)
            y = self.center_y + outer_radius * math.sin(angle)
            points.extend([x, y])
            
        # Inner arc (reverse order)
        for i in range(steps, -1, -1):
            angle = start_rad + (end_rad - start_rad) * i / steps
            x = self.center_x + inner_radius * math.cos(angle)
            y = self.center_y + inner_radius * math.sin(angle)
            points.extend([x, y])
            
        # Draw polygon
        if len(points) >= 6:  # Need at least 3 points
            arc_id = self.canvas.create_polygon(
                points,
                fill=color,
                outline="white",
                width=1
            )
            
            # Store segment info
            self.segments.append({
                'id': arc_id,
                'node': node,
                'inner_radius': inner_radius,
                'outer_radius': outer_radius,
                'start_angle': start_angle,
                'end_angle': end_angle
            })


class SizeBarChart:
    """Creates bar chart visualization for top space consumers"""
    
    def __init__(self, canvas: Canvas):
        self.canvas = canvas
        self.bars = []
        
    def draw_bars(self, items: List[dict], max_items: int = 20):
        """Draw horizontal bar chart of largest items"""
        self.canvas.delete("all")
        self.bars = []
        
        # Get canvas dimensions
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        
        if width <= 1 or height <= 1:
            self.canvas.after(100, lambda: self.draw_bars(items, max_items))
            return
            
        # Sort and limit items
        sorted_items = sorted(items, key=lambda x: x.get('size', 0), reverse=True)
        display_items = sorted_items[:max_items]
        
        if not display_items:
            return
            
        # Calculate dimensions
        margin = 10
        bar_height = (height - 2 * margin) // len(display_items)
        bar_height = min(bar_height, 30)  # Cap bar height
        max_size = display_items[0].get('size', 1)
        
        # Draw bars
        y = margin
        for i, item in enumerate(display_items):
            size = item.get('size', 0)
            bar_width = int((width - 2 * margin - 150) * (size / max_size))
            
            # Choose color
            hue = i / len(display_items)
            rgb = colorsys.hsv_to_rgb(hue, 0.6, 0.7)
            color = '#{:02x}{:02x}{:02x}'.format(
                int(rgb[0] * 255),
                int(rgb[1] * 255),
                int(rgb[2] * 255)
            )
            
            # Draw bar
            bar_id = self.canvas.create_rectangle(
                margin + 150, y,
                margin + 150 + bar_width, y + bar_height - 2,
                fill=color,
                outline="white"
            )
            
            # Draw label
            label = item.get('name', 'Unknown')
            if len(label) > 20:
                label = label[:17] + "..."
            
            self.canvas.create_text(
                margin, y + bar_height // 2,
                text=label,
                anchor="w",
                fill="black",
                font=("Arial", 9)
            )
            
            # Draw size
            size_text = self.format_size(size)
            self.canvas.create_text(
                margin + 145, y + bar_height // 2,
                text=size_text,
                anchor="e",
                fill="black",
                font=("Arial", 9)
            )
            
            y += bar_height
            
    def format_size(self, size: int) -> str:
        """Format size in human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}PB"


# This module will be integrated with the main application in Phase 2
# It provides advanced visualization options for disk usage analysis
