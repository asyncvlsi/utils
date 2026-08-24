#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Improved Waveform Viewer for PRN files - Optimized for Large Files
Author: Luis Zuniga
"""

import sys
import os
import numpy as np
import pandas as pd
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QFrame, QTreeView, QPushButton, QMenu, QAction, 
    QFileDialog, QMessageBox, QInputDialog, QTabWidget, QLabel,
    QCheckBox, QGroupBox, QFormLayout, QSlider, QComboBox,
    QListWidget, QListWidgetItem, QScrollArea, QRadioButton,
    QSizePolicy, QToolButton, QLineEdit, QDialog, QProgressBar,
    QSpinBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QModelIndex, QSize, QThread, QTimer
from PyQt5.QtGui import QFont, QColor, QStandardItemModel, QStandardItem
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
import matplotlib.style as style
import re
import gc
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import io

# Try to import psutil for memory monitoring, fallback if not available
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("Warning: psutil not available. Memory monitoring will be limited.")

# Global dictionary to store all loaded data
df_dict = {}

# Enable debug mode to show parsing details
DEBUG_MODE = False

# Default mangling configuration (can be overridden by user)
MANGLE_CHARS = ":()<>[],{}.\"$\\"
MANGLE_LETTER = "_"

# File loading optimizations
CHUNK_SIZE = 10000  # Number of lines to process at once
MAX_MEMORY_MB = 1000  # Maximum memory to use in MB
SAMPLE_FACTOR = 1  # Sample every Nth point (1 = no sampling)

def get_memory_usage():
    """Get current memory usage in MB"""
    if PSUTIL_AVAILABLE:
        try:
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / 1024 / 1024
        except Exception:
            pass
    
    # Fallback: estimate from garbage collector
    try:
        import gc
        return len(gc.get_objects()) * 0.001  # Very rough estimate
    except Exception:
        return 0.0

class FileLoaderThread(QThread):
    """Thread for loading large files in the background"""
    progress_update = pyqtSignal(int, str)  # progress percentage, status message
    data_loaded = pyqtSignal(object, str)  # dataframe, file_path
    error_occurred = pyqtSignal(str)  # error message
    
    def __init__(self, file_path, sample_factor=1, max_memory_mb=1000):
        super().__init__()
        self.file_path = file_path
        self.sample_factor = sample_factor
        self.max_memory_mb = max_memory_mb
        self.should_stop = False
        self.total_lines = 0 # Store total lines for progress calculation
        self.all_dfs = [] # List to store dataframes from each chunk
        
    def stop_loading(self):
        """Stop the loading process"""
        self.should_stop = True
        
    def run(self):
        """Load the file in the background"""
        try:
            self.progress_update.emit(0, "Starting file analysis...")
            
            # First, get file size and estimate memory requirements
            file_size = os.path.getsize(self.file_path)
            file_size_mb = file_size / (1024 * 1024)
            
            self.progress_update.emit(5, f"File size: {file_size_mb:.1f} MB")
            
            # Quick scan to count lines and detect structure
            # We need to read the file twice: once to count lines and headers, once to read data.
            # This could be optimized if the file format guarantees header is always first line.
            line_count = 0
            header_line = None
            
            with open(self.file_path, 'r') as f:
                header_line = f.readline().strip()
                for line_num, line in enumerate(f):
                    # Skip empty lines, comments, and end markers
                    if not line.strip() or line.strip().startswith("*") or line.strip().startswith("End"):
                        continue
                    line_count += 1
                    if line_count % 100000 == 0:
                        self.progress_update.emit(10, f"Analyzing file... {line_count:,} data lines")
                        if self.should_stop:
                            return
            
            self.total_lines = line_count # Store total lines for progress
            self.progress_update.emit(15, f"Found {line_count:,} data lines")
            
            # Parse header
            header_parts = re.split(r'\s+', header_line)
            clean_headers = []
            for header in header_parts:
                clean_header = re.sub(r'[^a-zA-Z0-9_:()]', '', header)
                clean_headers.append(clean_header)
            
            # Estimate memory usage and determine sampling
            # A very rough estimate: each float takes 8 bytes. Multiply by 2 for safety.
            estimated_memory = (line_count * len(clean_headers) * 8 * 2) / (1024 * 1024)
            
            if estimated_memory > self.max_memory_mb and self.sample_factor == 1:
                self.sample_factor = max(1, int(estimated_memory / self.max_memory_mb))
                self.progress_update.emit(20, f"Large file detected. Auto-sampling to factor: {self.sample_factor}")
            elif estimated_memory > self.max_memory_mb and self.sample_factor > 1:
                self.progress_update.emit(20, f"Large file detected. Already using sampling factor: {self.sample_factor}")
            else:
                self.progress_update.emit(20, "File size manageable, no sampling applied (or already set).")
            
            self.progress_update.emit(25, "Loading data...")
            
            # Using a list to collect lines for pandas.read_csv in chunks
            data_lines = [header_line] # Include header for pandas
            lines_processed = 0
            
            with open(self.file_path, 'r') as f:
                f.readline() # Skip header as it's already in data_lines
                for line_num, line in enumerate(f):
                    if self.should_stop:
                        return
                        
                    # Apply sampling
                    if self.sample_factor > 1 and (line_num % self.sample_factor != 0):
                        continue
                        
                    # Skip empty lines, comments, and end markers
                    if not line.strip() or line.strip().startswith("*") or line.strip().startswith("End"):
                        continue
                    
                    data_lines.append(line)
                    lines_processed += 1
                    
                    if len(data_lines) >= CHUNK_SIZE + 1: # +1 for header line
                        self._process_chunk(data_lines, clean_headers, lines_processed)
                        data_lines = [header_line] # Reset for next chunk, keep header
                        
                        progress = 25 + (lines_processed / self.total_lines) * 60 if self.total_lines > 0 else 85
                        self.progress_update.emit(int(progress), f"Processed {lines_processed:,} lines")
                        
                        # Check memory usage more frequently
                        current_memory = get_memory_usage()
                        if current_memory > self.max_memory_mb:
                            self.progress_update.emit(int(progress), f"Memory limit reached ({current_memory:.1f} MB), stopping early.")
                            break
                
            # Process any remaining lines
            if len(data_lines) > 1: # More than just the header
                self._process_chunk(data_lines, clean_headers, lines_processed)
            
            self.progress_update.emit(90, "Finalizing DataFrame...")
            
            # Concatenate all chunked dataframes
            if hasattr(self, 'all_dfs'):
                df = pd.concat(self.all_dfs, ignore_index=True)
            else:
                df = pd.DataFrame(columns=clean_headers) # Empty DataFrame if no data
            
            # Detect Xyce Monte Carlo output. Xyce writes all simulations into one
            # PRN table and resets TIME to zero at the start of each simulation.
            # Split those repeated time sweeps into one DataFrame row per simulation
            # so matplotlib never connects the end of one run to the start of the next.
            is_monte_carlo = False
            if len(df) > 1 and len(df.columns) > 1:
                time_column = df.columns[1]
                time_values = pd.to_numeric(df[time_column], errors='coerce').to_numpy()
                reset_indices = np.where(np.diff(time_values) < 0)[0] + 1

                if len(reset_indices) > 0:
                    starts = np.r_[0, reset_indices]
                    ends = np.r_[reset_indices, len(df)]
                    simulations = []

                    for start, end in zip(starts, ends):
                        sim = df.iloc[start:end]
                        simulations.append({
                            column: sim[column].to_numpy()
                            for column in df.columns
                        })

                    df = pd.DataFrame(simulations, columns=df.columns)
                    is_monte_carlo = True

            if is_monte_carlo:
                self.progress_update.emit(95, f"Detected Monte Carlo data with {len(df)} simulations. Preparing for display...")
            
            self.progress_update.emit(100, f"Loaded successfully! Memory usage: {get_memory_usage():.1f} MB")
            self.data_loaded.emit(df, self.file_path)
            
        except Exception as e:
            self.error_occurred.emit(f"Error loading file: {str(e)}")
            import traceback
            traceback.print_exc()
            
    def _process_chunk(self, chunk_lines, headers, total_lines_processed):
        """Process a chunk of lines using pandas.read_csv"""
        # Convert list of lines to a single string, then to a StringIO object
        chunk_string = "\n".join(chunk_lines)
        chunk_io = io.StringIO(chunk_string)
        
        try:
            # Use pandas.read_csv to parse the chunk
            # sep='\s+' handles variable whitespace
            # header=0 indicates the first line of the chunk_string is the header
            # engine='python' to handle skipfooter if needed, though not directly used here for chunks
            chunk_df = pd.read_csv(chunk_io, sep=r'\s+', header=0, engine='python')
            
            # Ensure column names are clean and match expected headers after pandas parsing
            # Pandas might add its own prefix if headers are duplicated or malformed
            # A simpler approach is to let pandas infer and then re-align/rename if necessary
            # For now, we assume pandas correctly parses based on sep='\s+'
            
            # Append to the list of dataframes
            if not hasattr(self, 'all_dfs'):
                self.all_dfs = []
            self.all_dfs.append(chunk_df)
            
        except Exception as e:
            self.error_occurred.emit(f"Error processing data chunk: {str(e)}")
            if DEBUG_MODE:
                print(f"Problematic chunk (first 500 chars): {chunk_string[:500]}")
                import traceback
                traceback.print_exc()

def unmangle_signal_name(mangled_name, convert_to_act_style=True):
    """
    Convert a mangled signal name back to its original form and optionally to native ACT style.
    
    For example, a signal named "x.y" would be mangled to "x_0y",
    and this function will convert it back to "x.y".
    
    If convert_to_act_style is True:
    - xfoo.xbar.xbaz is converted to foo.bar.baz.
    - Colons (:) are replaced with dots (.)
    - The result is converted to lowercase
    - SPICE voltage format V(...) is preserved
    
    Follows the ACT mangling spec:
    - _0 to _9 map to the first 10 characters in MANGLE_CHARS
    - _a to _z map to the characters beyond the first 10
    - __ represents the mangle letter itself (_)
    
    This function is case-insensitive to handle simulators like XYCE that 
    convert everything to uppercase.
    
    Args:
        mangled_name (str): The mangled signal name
        convert_to_act_style (bool): Whether to convert to native ACT style
    
    Returns:
        str: The unmangled signal name
    """
    if not mangled_name:
        return mangled_name
        
    # Convert mangle letter to lowercase for comparison
    mangle_letter_lower = MANGLE_LETTER.lower()
    mangle_letter_upper = MANGLE_LETTER.upper()
    
    # Log original name if in debug mode
    if DEBUG_MODE:
        print(f"Unmangling: {mangled_name}")
    
    # First, do the basic unmangling
    result = ""
    i = 0
    while i < len(mangled_name):
        # Check for mangled sequence (_0, _1, etc. or _0, _1, etc.)
        current_char = mangled_name[i]
        is_mangle_letter = (current_char.lower() == mangle_letter_lower)
        
        if is_mangle_letter and i + 1 < len(mangled_name):
            next_char = mangled_name[i + 1]
            
            # Handle repeated mangle letter (e.g., "__" or "__" means "_")
            if next_char.lower() == mangle_letter_lower:
                result += MANGLE_LETTER  # Use the original mangle letter
                i += 2
            # Handle digit mapping (0-9)
            elif next_char.isdigit():
                index = int(next_char)
                if index < len(MANGLE_CHARS):
                    result += MANGLE_CHARS[index]
                    i += 2
                else:
                    # Invalid index, keep as is
                    result += current_char
                    i += 1
            # Handle letter mapping (a-z or A-Z)
            elif next_char.isalpha():
                # Convert to lowercase for consistent indexing
                letter = next_char.lower()
                index = ord(letter) - ord('a') + 10  # a maps to 10, b to 11, etc.
                if index < len(MANGLE_CHARS):
                    result += MANGLE_CHARS[index]
                    i += 2
                else:
                    # Invalid index, keep as is
                    result += current_char
                    i += 1
            else:
                # Not a valid mangling sequence, keep as is
                result += current_char
                i += 1
        else:
            result += current_char
            i += 1
            
    # Now convert to native ACT style if requested

    if convert_to_act_style:
        # Remove extra subcircuit "x" characters
        if result.find(":") != -1:
            tmp = result.find(":")
            pos = 0
            result_new = ""
            while result[tmp+1:].find(":") != -1:
                new_tmp = result[tmp+1:].find(":") + tmp + 1
                result_new = result_new + result[pos:tmp+1]
                if (result[tmp+1] != "X" and result[tmp+1] != "x"):
                    print("Warning: Expecting subcircuit X in name", result)
                pos = tmp+2
                tmp = new_tmp
            result_new = result_new + result[pos:]
            result = result_new
            pos = result.find("(x")
            if pos == -1:
                pos = result.find("(X")
            if pos != -1:
                result = result[:pos+1] + result[pos+2:]

        # Special handling for SPICE format (V(...))
        spice_match = re.match(r'([A-Za-z]+)\((.*)\)$', result)
        if spice_match:
            spice_prefix = spice_match.group(1)
            node_content = spice_match.group(2)
            
            # Replace colons with dots in the node content
            node_content = node_content.replace(':', '.')
            
            # Convert to lowercase (but keep the SPICE prefix case as is)
            node_content = node_content.lower()
            
            # Reassemble the result
            result = f"{spice_prefix}({node_content})"
        else:
            # Not a SPICE format, just replace and convert
            result = result.replace(':', '.').lower()
    
    if DEBUG_MODE:
        print(f"  -> Result: {result}")
    
    return result

def debug_specific_signal(mangled_name, mangle_chars=None, mangle_letter=None):
    """
    Debug function to test unmangling a specific signal with explicit parameters.
    Prints every step of the process for troubleshooting.
    """
    if mangle_chars is None:
        mangle_chars = MANGLE_CHARS
    if mangle_letter is None:
        mangle_letter = MANGLE_LETTER
        
    # Convert mangle letter to lowercase for comparison
    mangle_letter_lower = mangle_letter.lower()
    mangle_letter_upper = mangle_letter.upper()
        
    print(f"\n==== Debugging unmangling of signal: {mangled_name} ====")
    print(f"Using mangle_chars: '{mangle_chars}'")
    print(f"Using mangle_letter: '{mangle_letter}' (case-insensitive)")
    
    result = ""
    i = 0
    while i < len(mangled_name):
        current_char = mangled_name[i]
        next_char = mangled_name[i+1] if i+1 < len(mangled_name) else None
        
        # Check if current char matches mangle letter (case-insensitive)
        is_mangle_letter = (current_char.lower() == mangle_letter_lower)
        
        # Print current state
        print(f"\nPosition {i}: '{current_char}'")
        if next_char:
            print(f"Next char: '{next_char}'")
            
        # Check for mangled sequence
        if is_mangle_letter and next_char is not None:
            # Check if next char matches mangle letter (case-insensitive)
            next_is_mangle = (next_char.lower() == mangle_letter_lower)
            
            if next_is_mangle:
                print(f"Found double mangle letter: '{current_char}{next_char}' -> '{mangle_letter}'")
                result += mangle_letter
                i += 2
            elif next_char.isdigit():
                index = int(next_char)
                if index < len(mangle_chars):
                    unmangled = mangle_chars[index]
                    print(f"Found digit index: '{current_char}{next_char}' -> '{unmangled}' (index {index})")
                    result += unmangled
                    i += 2
                else:
                    print(f"Invalid digit index {index}, keeping as is")
                    result += current_char
                    i += 1
            elif next_char.isalpha():
                # Convert to lowercase for consistent indexing
                letter = next_char.lower()
                index = ord(letter) - ord('a') + 10  # a maps to 10, b to 11, etc.
                if index < len(mangle_chars):
                    unmangled = mangle_chars[index]
                    print(f"Found letter index: '{current_char}{next_char}' -> '{unmangled}' (index {index})")
                    result += unmangled
                    i += 2
                else:
                    print(f"Invalid letter index {index}, keeping as is")
                    result += current_char
                    i += 1
            else:
                print(f"Not a valid mangling sequence, keeping as is")
                result += current_char
                i += 1
        else:
            print(f"Regular character, keeping as is")
            result += current_char
            i += 1
            
        print(f"Result so far: '{result}'")
    
    print(f"\n==== Final result: '{result}' ====\n")
    return result

def debug_voltage_signal(self):
    """Debug the problematic voltage signal"""
    test_signal = "V(XT_1XWD_1XW_5_0_6_1DIN)"
    uppercase_test = "V(XT_1XWD_1XW_5_0_6_1DIN)"  # Same but with uppercase
    multiple_test = "X_0Y and A_1B_2C with _5_9_a too"
    
    # Show a message with information
    msg = QMessageBox()
    msg.setWindowTitle("Debugging Signals")
    msg.setText(f"Testing unmangling for:\n\n" +
               f"1. Voltage signal: {test_signal}\n\n" +
               f"2. Uppercase signal: {uppercase_test}\n\n" +
               f"3. Multiple sequences: {multiple_test}")
    msg.setInformativeText("Check the console for detailed output.")
    msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    choice = msg.exec_()
    
    if choice == QMessageBox.Cancel:
        return
        
    # Test with current mapping
    print("\n=== Testing voltage signal with current mapping ===")
    voltage_result = debug_specific_signal(test_signal)
    
    print("\n=== Testing uppercase signal with current mapping ===")
    uppercase_result = debug_specific_signal(uppercase_test)
    
    print("\n=== Testing multiple sequences with current mapping ===")
    multiple_result = debug_specific_signal(multiple_test)
    
    # Show results in a message box
    result_msg = QMessageBox()
    result_msg.setWindowTitle("Unmangling Results")
    result_msg.setText(f"Voltage signal:\n{test_signal} → {voltage_result}\n\n" +
                      f"Uppercase signal:\n{uppercase_test} → {uppercase_result}\n\n" +
                      f"Multiple sequences:\n{multiple_test} → {multiple_result}\n\n" +
                      "Check the console for detailed debugging information.")
    result_msg.setStandardButtons(QMessageBox.Ok)
    result_msg.exec_()

class SignalCheckbox(QCheckBox):
    """Custom checkbox for signal selection with data storage capabilities"""
    def __init__(self, text, original_name=None, data=None, color=None):
        super().__init__(text)
        self.original_name = original_name if original_name else text
        self.signal_data = data
        self.signal_color = color if color else "auto"
        
class PlotCanvas(FigureCanvas):
    """Canvas for matplotlib plots with enhanced zoom capabilities"""
    def __init__(self, parent=None, width=8, height=6, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = self.fig.add_subplot(111)
        
        # Set tight layout to maximize plot area
        self.fig.tight_layout(pad=1.5)
        
        FigureCanvas.__init__(self, self.fig)
        self.setParent(parent)
        
        # Set size policy to expand with main window
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()
        
        # Enable mouse wheel for zooming
        self.mpl_connect('scroll_event', self.zoom_event)
        # Enable panning with middle mouse button
        self.mpl_connect('button_press_event', self.on_press)
        self.mpl_connect('button_release_event', self.on_release)
        self.mpl_connect('motion_notify_event', self.on_motion)
        
        # Enable hover annotations
        self.hover_annot = self.axes.annotate("", xy=(0, 0), xytext=(20, 20),
                                            textcoords="offset points",
                                            bbox=dict(boxstyle="round", fc="w"),
                                            arrowprops=dict(arrowstyle="->"))
        self.hover_annot.set_visible(False)
        
        # Listen for hover events
        self.hover_enabled = True
        self.hover_lines = []
        self.hover_line_labels = []
        self.mpl_connect('motion_notify_event', self.hover)
        
        # Enable clicking on lines
        self.line_click_enabled = True
        self.lines = []  # Store line objects
        self.line_labels = []  # Store line labels
        self.original_linewidths = []  # Store original line widths
        self.highlighted_line = None  # Currently highlighted line
        self.mpl_connect('pick_event', self.on_pick)
        
        # Initialize panning variables
        self.panning = False
        self.pan_start_x = 0
        self.pan_start_y = 0
        
        # Initial plot settings
        self.axes.grid(True)
        self.axes.ticklabel_format(axis='both', style='sci', scilimits=(0,0))
        
    def zoom_event(self, event):
        """Handle mouse wheel zoom events"""
        if not event.inaxes:
            return
            
        # Get current axis limits
        x_min, x_max = self.axes.get_xlim()
        y_min, y_max = self.axes.get_ylim()
        
        # Zoom factor
        factor = 0.9 if event.button == 'up' else 1.1
        
        # Calculate new limits
        x_range = (x_max - x_min) * factor
        y_range = (y_max - y_min) * factor
        
        # Set the cursor location as the center of zoom
        x_center = event.xdata
        y_center = event.ydata
        
        # Set new limits
        self.axes.set_xlim([x_center - x_range/2, x_center + x_range/2])
        self.axes.set_ylim([y_center - y_range/2, y_center + y_range/2])
        
        self.draw()
        
    def on_press(self, event):
        """Handle button press events for panning"""
        if event.button == 2:  # Middle mouse button
            self.panning = True
            self.pan_start_x = event.xdata
            self.pan_start_y = event.ydata
            self.setCursor(Qt.ClosedHandCursor)
            
    def on_release(self, event):
        """Handle button release events for panning"""
        if self.panning and event.button == 2:
            self.panning = False
            self.setCursor(Qt.ArrowCursor)
            
    def on_motion(self, event):
        """Handle mouse motion events for panning"""
        if not self.panning or not event.inaxes:
            return
            
        dx = self.pan_start_x - event.xdata
        dy = self.pan_start_y - event.ydata
        
        x_min, x_max = self.axes.get_xlim()
        y_min, y_max = self.axes.get_ylim()
        
        self.axes.set_xlim(x_min + dx, x_max + dx)
        self.axes.set_ylim(y_min + dy, y_max + dy)
        
        self.draw()
        
    def hover(self, event):
        """Handle hover events to show annotations"""
        if event.inaxes == self.axes:
            visible = False
            for line, label in zip(self.lines, self.line_labels):
                cont, ind = line.contains(event)
                if cont:
                    self.update_annot(line, ind["ind"][0], label)
                    self.hover_annot.set_visible(True)
                    visible = True
                    break
            if not visible:
                self.hover_annot.set_visible(False)
            self.draw_idle()
            
    def update_annot(self, line, idx, label):
        """Update the annotation with the data point value"""
        # Get x,y data for the line
        x, y = line.get_data()
        
        # Set annotation position to data point
        self.hover_annot.xy = (x[idx], y[idx])
        
        # Extract base label (remove simulation details if present)
        base_label = label
        if "(" in label:
            base_label = label.split("(")[0].strip()
            
        # Format text with x,y values
        if abs(y[idx]) < 0.001 or abs(y[idx]) > 1000:
            # Use scientific notation for very small or very large values
            text = f"{base_label}\nx: {x[idx]:.4g}\ny: {y[idx]:.4e}"
        else:
            # Use regular notation for normal values
            text = f"{base_label}\nx: {x[idx]:.4g}\ny: {y[idx]:.4g}"
            
        self.hover_annot.set_text(text)
        
        # Update annotation appearance
        self.hover_annot.get_bbox_patch().set_alpha(0.9)
        
    def on_pick(self, event):
        """Handle pick events (clicking on lines)"""
        if not hasattr(event, 'artist') or not isinstance(event.artist, plt.Line2D):
            return
            
        # Get the line object that was clicked
        line = event.artist
        
        # Find line index in the lines list
        try:
            line_idx = self.lines.index(line)
        except ValueError:
            # Line not found in lines list
            return
            
        # Unhighlight previously highlighted line (if any)
        if self.highlighted_line is not None:
            try:
                old_idx = self.lines.index(self.highlighted_line)
                if old_idx < len(self.original_linewidths):
                    self.highlighted_line.set_linewidth(self.original_linewidths[old_idx])
            except (ValueError, IndexError):
                # Previous line not found or index out of range
                pass
            
        # Highlight the clicked line
        if line_idx < len(self.original_linewidths):
            # Double the line width
            line.set_linewidth(self.original_linewidths[line_idx] * 2)
        else:
            # Fallback if original width not available
            current_width = line.get_linewidth()
            line.set_linewidth(current_width * 2)
            
        # Store as highlighted line
        self.highlighted_line = line
        
        # Show annotation at clicked point
        ind = event.ind[0]  # Get index of closest data point
        if line_idx < len(self.line_labels):
            label = self.line_labels[line_idx]
            self.update_annot(line, ind, label)
            self.hover_annot.set_visible(True)
            
        # Redraw to update line width
        self.draw_idle()
        
    def plot_signals(self, time_arrays, signal_arrays, labels, colors=None, is_monte_carlo=False):
        """Plot multiple signals on the canvas"""
        try:
            # Clear previous plot
            self.clear_plot()
            
            # Store signal data for hover functionality
            self.signal_data = []
            for i, (time, signal) in enumerate(zip(time_arrays, signal_arrays)):
                self.signal_data.append((time, signal))
            
            # Default colors if not provided
            if not colors:
                # Use matplotlib's default color cycle
                prop_cycle = plt.rcParams['axes.prop_cycle']
                colors = prop_cycle.by_key()['color']
            
            # Reset lines and labels storage
            self.lines = []
            self.line_labels = []
            self.hover_lines = []
            self.hover_line_labels = []
            self.original_linewidths = []
            
            # Create lines for each signal
            for i, (time, signal) in enumerate(zip(time_arrays, signal_arrays)):
                # Get color for this line
                color_idx = i % len(colors)
                color = colors[color_idx]
                
                # Skip empty arrays
                if len(time) == 0 or len(signal) == 0:
                    continue
                
                # Make sure arrays have the same length
                if len(time) != len(signal):
                    min_len = min(len(time), len(signal))
                    time = time[:min_len]
                    signal = signal[:min_len]
                
                # Create label - store both original and simplified versions
                if i < len(labels):
                    # Store full label for legend
                    full_label = labels[i]
                    
                    # For hover/pick events, extract base signal name
                    base_label = full_label
                    if is_monte_carlo and "(" in full_label:
                        base_label = full_label.split("(")[0].strip()

                    # Plot line with full label for legend
                    line, = self.axes.plot(time, signal, label=full_label, color=color, picker=5)
                    
                    # Store original line width
                    self.original_linewidths.append(line.get_linewidth())
                    
                    # Store line objects and base labels for hover/pick functionality
                    self.lines.append(line)
                    self.line_labels.append(base_label)
                    self.hover_lines.append(line)
                    self.hover_line_labels.append(base_label)
                else:
                    # No label provided, use empty string
                    line, = self.axes.plot(time, signal, label="", color=color, picker=5)
                    self.original_linewidths.append(line.get_linewidth())
                    self.lines.append(line)
                    self.line_labels.append("")
                    self.hover_lines.append(line)
                    self.hover_line_labels.append("")
            
            # Configure the plot
            self.axes.grid(True, linestyle='--', alpha=0.7)
            self.axes.set_xlabel('Time')
            self.axes.set_ylabel('V')
            
            # Auto-scale
            self.axes.relim()
            self.axes.autoscale()
            
            # Draw the plot
            self.draw()
            
            # Update legend panel in parent tab if available
            parent_tab = self.parent()
            if parent_tab and hasattr(parent_tab, 'update_legend_panel'):
                # For Monte Carlo data with many simulations
                if is_monte_carlo:
                    # Group lines by signal name
                    unique_labels = []
                    unique_lines = []
                    unique_colors = []
                    
                    # Get base signal names (without simulation details)
                    for i, label in enumerate(labels):
                        base_label = label
                        if "(" in label:
                            base_label = label.split("(")[0].strip()
                            
                        # Only add unique signal names
                        if i < len(self.lines) and base_label not in unique_labels:
                            unique_labels.append(base_label)
                            unique_lines.append(self.lines[i])
                            unique_colors.append(self.lines[i].get_color())
                    
                    # Update legend with unique signal names
                    parent_tab.update_legend_panel(unique_lines, unique_labels, unique_colors)
                else:
                    # For regular data, show all lines
                    parent_tab.update_legend_panel(self.lines, self.line_labels, [l.get_color() for l in self.lines])
                
        except Exception as e:
            print(f"Error in plotting: {e}")
            import traceback
            traceback.print_exc()
        
    def clear_plot(self):
        """Clear the plot"""
        self.axes.clear()
        self.lines = []
        self.line_labels = []
        self.hover_lines = []
        self.hover_line_labels = []
        self.original_linewidths = []
        self.highlighted_line = None
        self.draw()


class PlotTab(QWidget):
    """Tab widget containing a plot canvas and controls"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(0)  # Reduce spacing to minimum
        self.layout.setContentsMargins(1, 1, 1, 1)  # Minimum margins
        
        # Create toolbar with plot-specific controls
        self.create_toolbar()
        
        # Container for multiple plot frames
        self.plots_container = QWidget()
        self.plots_layout = QVBoxLayout(self.plots_container)
        self.plots_layout.setSpacing(0)
        self.plots_layout.setContentsMargins(0, 0, 0, 0)
        
        # Create legend panel
        self.legend_panel = QFrame()
        self.legend_panel.setFrameShape(QFrame.StyledPanel)
        self.legend_panel.setMinimumWidth(150)
        self.legend_panel.setMaximumWidth(200)
        self.legend_layout = QVBoxLayout(self.legend_panel)
        self.legend_layout.setSpacing(1)
        self.legend_layout.setContentsMargins(2, 2, 2, 2)
        self.legend_title = QLabel("Signal Legend")
        self.legend_title.setAlignment(Qt.AlignCenter)
        self.legend_title.setFont(QFont("Arial", 9, QFont.Bold))
        self.legend_layout.addWidget(self.legend_title)
        
        # Container for legend items
        self.legend_container = QWidget()
        self.legend_container_layout = QVBoxLayout(self.legend_container)
        self.legend_container_layout.setSpacing(1)
        self.legend_container_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_scroll = QScrollArea()
        self.legend_scroll.setWidgetResizable(True)
        self.legend_scroll.setWidget(self.legend_container)
        self.legend_layout.addWidget(self.legend_scroll)
        
        # Create main splitter between plots and legend
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.addWidget(self.plots_container)
        self.main_splitter.addWidget(self.legend_panel)
        self.main_splitter.setSizes([800, 200])
        
        # Hide legend panel initially
        self.legend_panel.setVisible(False)
        
        # Add first plot
        self.add_plot()
        
        # Create compact plot controls
        self.create_plot_controls()
        
        # Add main components to layout
        self.layout.addWidget(self.main_splitter, 1)  # Give plot area most of the space
        self.layout.addWidget(self.plot_controls, 0)  # Minimum space for controls
        
    def mpl_to_qcolor(self, mpl_color):
        """Convert matplotlib color to QColor"""
        # Handle RGB/RGBA tuples
        if isinstance(mpl_color, tuple) and len(mpl_color) in (3, 4):
            if len(mpl_color) == 3:
                r, g, b = mpl_color
                a = 255
            else:
                r, g, b, a = mpl_color
                
            # Convert from 0-1 scale to 0-255
            if all(0 <= c <= 1 for c in mpl_color):
                r = int(r * 255)
                g = int(g * 255)
                b = int(b * 255)
                if len(mpl_color) == 4:
                    a = int(a * 255)
            
            return QColor(r, g, b, a)
            
        # Handle standard color names
        elif isinstance(mpl_color, str):
            # Remove '#' if present for hex codes
            if mpl_color.startswith('#'):
                color_str = mpl_color[1:]
            else:
                color_str = mpl_color
                
            return QColor(color_str)
        
        # Default fallback to black
        return QColor(0, 0, 0)
        
    def create_toolbar(self):
        """Create toolbar for plot-specific controls"""
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(1, 1, 1, 1)
        
        # Add plot button - make it smaller and more attractive
        self.add_plot_btn = QPushButton("+ Add Graph")
        self.add_plot_btn.setFixedHeight(24)
        self.add_plot_btn.setStyleSheet("font-size: 11px;")
        self.add_plot_btn.clicked.connect(self.add_plot)
        self.add_plot_btn.setToolTip("Add another graph below")
        toolbar_layout.addWidget(self.add_plot_btn)
        
        # Remove plot button - make it smaller and more attractive
        self.remove_plot_btn = QPushButton("- Remove Graph")
        self.remove_plot_btn.setFixedHeight(24)
        self.remove_plot_btn.setStyleSheet("font-size: 11px;")
        self.remove_plot_btn.clicked.connect(self.remove_plot)
        self.remove_plot_btn.setToolTip("Remove last graph")
        toolbar_layout.addWidget(self.remove_plot_btn)
        
        # Add spacer
        toolbar_layout.addStretch(1)
        
        # Add toolbar to main layout
        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar_layout)
        toolbar_widget.setFixedHeight(30)
        self.layout.addWidget(toolbar_widget)
    
    def add_plot(self):
        """Add a new plot canvas to the tab"""
        # Create plot frame
        plot_frame = QFrame()
        plot_frame.setFrameShape(QFrame.StyledPanel)
        plot_layout = QVBoxLayout(plot_frame)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(0)
        
        # Create plot canvas and toolbar
        canvas = PlotCanvas(self)
        toolbar = NavigationToolbar(canvas, self)
        toolbar.setIconSize(QSize(16, 16))  # Make toolbar icons smaller
        toolbar.setFixedHeight(28)  # Make toolbar more compact
        
        # Add components to layout
        plot_layout.addWidget(toolbar)
        plot_layout.addWidget(canvas)
        
        # Add to container
        self.plots_layout.addWidget(plot_frame)
        
        # Update controls state
        self.remove_plot_btn.setEnabled(self.plots_layout.count() > 1)
        
        return canvas
    
    def remove_plot(self):
        """Remove the last plot"""
        if self.plots_layout.count() > 1:
            # Get the last plot frame
            plot_frame = self.plots_layout.itemAt(self.plots_layout.count() - 1).widget()
            if plot_frame:
                # Remove and delete
                self.plots_layout.removeWidget(plot_frame)
                plot_frame.deleteLater()
                
                # Update controls state
                self.remove_plot_btn.setEnabled(self.plots_layout.count() > 1)
                
    def toggle_legend_panel(self, checked):
        """Toggle visibility of the legend panel"""
        self.legend_panel.setVisible(checked)
        
    def get_active_canvas(self):
        """Get the last/active plot canvas"""
        if self.plots_layout.count() > 0:
            # Get the last plot frame
            plot_frame = self.plots_layout.itemAt(self.plots_layout.count() - 1).widget()
            if plot_frame:
                # Find the canvas in the plot frame
                for i in range(plot_frame.layout().count()):
                    widget = plot_frame.layout().itemAt(i).widget()
                    if isinstance(widget, PlotCanvas):
                        return widget
        return None
        
    def update_legend_panel(self, lines, labels, colors):
        """Update the legend panel with current signals"""
        # Clear existing legend items
        for i in reversed(range(self.legend_container_layout.count())): 
            item = self.legend_container_layout.itemAt(i)
            if item and item.widget():  # Check if widget exists
                item.widget().deleteLater()
        
        # Add new legend items
        for i, (line, label) in enumerate(zip(lines, labels)):
            if not label:  # Skip empty labels
                continue
                
            # Create legend item widget
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(0, 2, 0, 2)
            
            # Color indicator
            color_indicator = QLabel()
            color_indicator.setFixedSize(16, 16)
            
            # Safely convert matplotlib color to QColor
            mpl_color = line.get_color()
            qt_color = self.mpl_to_qcolor(mpl_color)
            
            style_sheet = f"background-color: {qt_color.name()}; border: 1px solid black;"
            color_indicator.setStyleSheet(style_sheet)
            
            # Label
            item_label = QLabel(label)
            item_label.setFont(QFont("Arial", 9))
            
            # Add to layout
            item_layout.addWidget(color_indicator)
            item_layout.addWidget(item_label, 1)
            
            # Add to container
            self.legend_container_layout.addWidget(item_widget)
        
        # Add stretch at the end
        self.legend_container_layout.addStretch(1)

    def create_plot_controls(self):
        """Create controls for plot customization"""
        self.plot_controls = QWidget()  # Use QWidget, not QGroupBox to save space
        self.plot_controls.setFixedHeight(36)  # Even smaller fixed height
        
        controls_layout = QHBoxLayout(self.plot_controls)
        controls_layout.setSpacing(5)  # Reduce spacing
        controls_layout.setContentsMargins(2, 0, 2, 0)  # Minimum vertical margins
        
        # Create a more compact horizontal layout
        # Axis controls in a single horizontal layout
        axis_controls = QWidget()
        axis_layout = QHBoxLayout(axis_controls)
        axis_layout.setContentsMargins(1, 1, 1, 1)  # Minimal margins
        axis_layout.setSpacing(2)  # Minimal spacing
        
        # X-axis controls
        x_layout = QHBoxLayout()
        x_layout.setSpacing(0)
        x_label = QLabel("X:")
        x_label.setFont(QFont("Arial", 9))
        self.x_linear = QRadioButton("Lin")
        self.x_linear.setFont(QFont("Arial", 9))
        self.x_log = QRadioButton("Log")
        self.x_log.setFont(QFont("Arial", 9))
        self.x_linear.setChecked(True)
        x_layout.addWidget(x_label)
        x_layout.addWidget(self.x_linear)
        x_layout.addWidget(self.x_log)
        
        # Separator
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.VLine)
        separator1.setFrameShadow(QFrame.Sunken)
        
        # Y-axis controls
        y_layout = QHBoxLayout()
        y_layout.setSpacing(0)
        y_label = QLabel("Y:")
        y_label.setFont(QFont("Arial", 9))
        self.y_linear = QRadioButton("Lin")
        self.y_linear.setFont(QFont("Arial", 9))
        self.y_log = QRadioButton("Log")
        self.y_log.setFont(QFont("Arial", 9))
        self.y_linear.setChecked(True)
        y_layout.addWidget(y_label)
        y_layout.addWidget(self.y_linear)
        y_layout.addWidget(self.y_log)
        
        # Add to axis layout
        axis_layout.addLayout(x_layout)
        axis_layout.addWidget(separator1)
        axis_layout.addLayout(y_layout)
        
        # Connect signals
        self.x_linear.toggled.connect(self.update_x_scale)
        self.x_log.toggled.connect(self.update_x_scale)
        self.y_linear.toggled.connect(self.update_y_scale)
        self.y_log.toggled.connect(self.update_y_scale)
        
        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.VLine)
        separator2.setFrameShadow(QFrame.Sunken)
        
        # Display controls in a horizontal layout
        display_layout = QHBoxLayout()
        display_layout.setSpacing(5)
        
        self.grid_on = QCheckBox("Grid")
        self.grid_on.setFont(QFont("Arial", 9))
        self.grid_on.setChecked(True)
        self.grid_on.toggled.connect(self.update_grid)
        
        self.legend_in_plot = QCheckBox("Legend")
        self.legend_in_plot.setFont(QFont("Arial", 9))
        self.legend_in_plot.setChecked(False)
        self.legend_in_plot.toggled.connect(self.update_legend_location)
        
        self.show_legend_panel = QCheckBox("Side Panel")
        self.show_legend_panel.setFont(QFont("Arial", 9))
        self.show_legend_panel.setChecked(False)
        self.show_legend_panel.toggled.connect(self.toggle_legend_panel)
        
        # Add to display layout
        display_layout.addWidget(self.grid_on)
        display_layout.addWidget(self.legend_in_plot)
        display_layout.addWidget(self.show_legend_panel)
        
        # Separator
        separator3 = QFrame()
        separator3.setFrameShape(QFrame.VLine)
        separator3.setFrameShadow(QFrame.Sunken)
        
        # Reset view button
        reset_button = QPushButton("Reset View")
        reset_button.setFont(QFont("Arial", 9))
        reset_button.setFixedHeight(24)
        reset_button.clicked.connect(self.reset_view)
        
        # Add all control groups to layout
        controls_layout.addWidget(axis_controls)
        controls_layout.addWidget(separator2)
        controls_layout.addLayout(display_layout)
        controls_layout.addWidget(separator3)
        controls_layout.addWidget(reset_button)
        controls_layout.addStretch(1)  # Add stretch to keep controls compact
        
    def update_y_scale(self):
        """Update the y-axis scale for all plots"""
        for i in range(self.plots_layout.count()):
            plot_frame = self.plots_layout.itemAt(i).widget()
            if plot_frame:
                for j in range(plot_frame.layout().count()):
                    widget = plot_frame.layout().itemAt(j).widget()
                    if isinstance(widget, PlotCanvas):
                        if self.y_log.isChecked():
                            widget.axes.set_yscale('log')
                        else:
                            widget.axes.set_yscale('linear')
                        widget.draw()
        
    def update_x_scale(self):
        """Update the x-axis scale for all plots"""
        for i in range(self.plots_layout.count()):
            plot_frame = self.plots_layout.itemAt(i).widget()
            if plot_frame:
                for j in range(plot_frame.layout().count()):
                    widget = plot_frame.layout().itemAt(j).widget()
                    if isinstance(widget, PlotCanvas):
                        if self.x_log.isChecked():
                            widget.axes.set_xscale('log')
                        else:
                            widget.axes.set_xscale('linear')
                        widget.draw()
        
    def update_grid(self):
        """Toggle grid visibility for all plots"""
        for i in range(self.plots_layout.count()):
            plot_frame = self.plots_layout.itemAt(i).widget()
            if plot_frame:
                for j in range(plot_frame.layout().count()):
                    widget = plot_frame.layout().itemAt(j).widget()
                    if isinstance(widget, PlotCanvas):
                        widget.axes.grid(self.grid_on.isChecked())
                        widget.draw()
        
    def update_legend_location(self, checked):
        """Update legend location for all plots"""
        for i in range(self.plots_layout.count()):
            plot_frame = self.plots_layout.itemAt(i).widget()
            if plot_frame:
                for j in range(plot_frame.layout().count()):
                    widget = plot_frame.layout().itemAt(j).widget()
                    if isinstance(widget, PlotCanvas):
                        if checked and widget.lines:
                            widget.axes.legend(loc='best')
                        elif widget.axes.get_legend():
                            widget.axes.get_legend().remove()
                        widget.draw()
        
    def reset_view(self):
        """Reset view to show all data for all plots"""
        for i in range(self.plots_layout.count()):
            plot_frame = self.plots_layout.itemAt(i).widget()
            if plot_frame:
                for j in range(plot_frame.layout().count()):
                    widget = plot_frame.layout().itemAt(j).widget()
                    if isinstance(widget, PlotCanvas):
                        widget.axes.autoscale(True)
                        widget.axes.relim()
                        widget.axes.autoscale_view()
                        widget.draw()


class SignalSelector(QFrame):
    """Widget for selecting signals to plot"""
    signal_selection_changed = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        
        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)  # Minimized margins
        
        # Add search box at the top
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(2, 2, 2, 2)
        
        search_label = QLabel("Search:")
        search_label.setFont(QFont("Arial", 9))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Type to search signals...")
        self.search_box.textChanged.connect(self.filter_signals)
        
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_box, 1)
        
        # Add clear button
        clear_search_btn = QPushButton("×")
        clear_search_btn.setFixedSize(20, 20)
        clear_search_btn.setToolTip("Clear search")
        clear_search_btn.clicked.connect(self.clear_search)
        search_layout.addWidget(clear_search_btn)
        
        layout.addLayout(search_layout)
        
        # Create options layout
        options_layout = QHBoxLayout()
        options_layout.setSpacing(5)
        
        # Add unmangle option
        self.unmangle_checkbox = QCheckBox("Unmangle ACT Names")
        self.unmangle_checkbox.setChecked(True)
        self.unmangle_checkbox.setToolTip("Convert mangled names back to original form (e.g., x_0y → x.y)")
        self.unmangle_checkbox.toggled.connect(self.refresh_signal_names)
        options_layout.addWidget(self.unmangle_checkbox)
        
        # Add ACT style option
        self.act_style_checkbox = QCheckBox("Native ACT Style")
        self.act_style_checkbox.setChecked(True)
        self.act_style_checkbox.setToolTip("Convert to native ACT style (replace : with . and lowercase)")
        self.act_style_checkbox.toggled.connect(self.refresh_signal_names)
        options_layout.addWidget(self.act_style_checkbox)
        
        layout.addLayout(options_layout)
        
        # Add status label to show search results statistics
        self.status_label = QLabel("No signals loaded")
        self.status_label.setAlignment(Qt.AlignLeft)
        self.status_label.setFont(QFont("Arial", 8))
        layout.addWidget(self.status_label)
        
        # Create scroll area for checkbox list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(scroll)
        
        # Create widget to hold checkboxes
        self.checkbox_container = QWidget()
        self.checkbox_layout = QVBoxLayout(self.checkbox_container)
        self.checkbox_layout.setContentsMargins(2, 2, 2, 2)
        self.checkbox_layout.setSpacing(1)  # Reduced spacing
        self.checkbox_layout.setAlignment(Qt.AlignTop)
        
        # Add stretch at the end
        self.checkbox_layout.addStretch(1)
        
        # Set container as scroll area widget
        scroll.setWidget(self.checkbox_container)
        
        # Store all checkboxes for filtering
        self.all_checkboxes = []
        self.filtered_signal_count = 0
        
        # Store original signal names
        self.original_signal_names = []
        
    def clear_search(self):
        """Clear the search box"""
        self.search_box.clear()
        
    def filter_signals(self, search_text):
        """Filter signals based on search text"""
        search_text = search_text.lower()
        
        # Count visible signals
        visible_count = 0
        total_count = len(self.all_checkboxes)
        
        # Show/hide checkboxes based on search text
        for checkbox in self.all_checkboxes:
            if search_text == "":
                # Show all when search is empty
                checkbox.setVisible(True)
                visible_count += 1
            else:
                # Show only if signal name contains search text
                if search_text in checkbox.text().lower():
                    checkbox.setVisible(True)
                    visible_count += 1
                else:
                    checkbox.setVisible(False)
        
        # Update the filtered count
        self.filtered_signal_count = visible_count
        
        # Update status indicator
        if hasattr(self, 'status_label'):
            if search_text == "":
                self.status_label.setText(f"Showing all {total_count} signals")
            else:
                self.status_label.setText(f"Showing {visible_count} of {total_count} signals")
        
        # Expand container height to show all visible checkboxes
        self.checkbox_container.updateGeometry()
    
    def refresh_signal_names(self):
        """Refresh signal names based on settings"""
        if not self.original_signal_names:
            return
            
        # Store current selection state
        selected_signals = self.get_selected_signals()
        
        # Reload signals with current settings
        self.add_signals(self.original_signal_names)
        
        # Restore selection state
        for checkbox in self.all_checkboxes:
            original_name = checkbox.original_name
            checkbox.setChecked(original_name in selected_signals)
        
    def add_signals(self, signal_names):
        """Add signal names to the list widget"""
        # Clear existing checkboxes first
        while self.checkbox_layout.count():
            item = self.checkbox_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Reset checkbox list
        self.all_checkboxes = []
        
        # Store original signal names
        self.original_signal_names = signal_names.copy()
        
        # Add new checkboxes
        for name in signal_names:
            # Determine display name based on settings
            if self.unmangle_checkbox.isChecked():
                convert_to_act = self.act_style_checkbox.isChecked()
                display_name = unmangle_signal_name(name, convert_to_act_style=convert_to_act)
            else:
                display_name = name
            
            # Create checkbox with display name but store original name
            checkbox = SignalCheckbox(display_name, original_name=name)
            checkbox.toggled.connect(self.emit_selection_changed)
            self.checkbox_layout.addWidget(checkbox)
            self.all_checkboxes.append(checkbox)
            
        # Add stretch at the end
        self.checkbox_layout.addStretch(1)
        
        # Update filtered count
        self.filtered_signal_count = len(self.all_checkboxes)
        
        # Update status label
        total_count = len(self.all_checkboxes)
        if total_count > 0:
            self.status_label.setText(f"Showing all {total_count} signals")
        else:
            self.status_label.setText("No signals loaded")
        
        # Apply current filter
        self.filter_signals(self.search_box.text())
            
    def get_selected_signals(self):
        """Get list of selected signal names (using original mangled names)"""
        selected_signals = []
        for checkbox in self.all_checkboxes:
            if checkbox.isChecked():
                selected_signals.append(checkbox.original_name)
        return selected_signals
    
    def emit_selection_changed(self):
        """Emit signal that selection has changed"""
        self.signal_selection_changed.emit()
        
    def clear_selection(self):
        """Clear all selections"""
        for checkbox in self.all_checkboxes:
            checkbox.setChecked(False)
        self.emit_selection_changed()
        
    def select_all(self):
        """Select all signals"""
        for checkbox in self.all_checkboxes:
            # Only select visible checkboxes if filtering is active
            if checkbox.isVisible():
                checkbox.setChecked(True)
        self.emit_selection_changed()
        
    def select_all_filtered(self):
        """Select all filtered (visible) signals"""
        for checkbox in self.all_checkboxes:
            if checkbox.isVisible():
                checkbox.setChecked(True)
        self.emit_selection_changed()
        
    def deselect_all(self):
        """Deselect all signals"""
        self.clear_selection()


class ManglingConfigDialog(QDialog):
    """Dialog to configure ACT name mangling settings"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ACT Name Mangling Configuration")
        self.setMinimumWidth(400)
        
        # Create layout
        layout = QVBoxLayout(self)
        
        # Add info label
        info_label = QLabel(
            "Configure how ACT mangled names are unmangled. " +
            "These settings should match your ACT configuration."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Add form for mangling settings
        form_layout = QFormLayout()
        
        # Mangle chars input
        self.mangle_chars_input = QLineEdit(MANGLE_CHARS)
        form_layout.addRow("Mangle Characters:", self.mangle_chars_input)
        
        # Mangle letter input
        self.mangle_letter_input = QLineEdit(MANGLE_LETTER)
        self.mangle_letter_input.setMaxLength(1)  # Only one character allowed
        form_layout.addRow("Mangle Letter:", self.mangle_letter_input)
        
        # Debug mode checkbox
        self.debug_checkbox = QCheckBox("Enable Debug Mode")
        self.debug_checkbox.setChecked(DEBUG_MODE)
        self.debug_checkbox.setToolTip("Show unmangling details in console for debugging")
        form_layout.addRow("", self.debug_checkbox)
        
        # Test specific signal
        self.test_signal_input = QLineEdit()
        self.test_signal_input.setPlaceholderText("Enter a signal name to test unmangling")
        form_layout.addRow("Test Signal:", self.test_signal_input)
        
        test_button = QPushButton("Test Unmangling")
        test_button.clicked.connect(self.test_unmangling)
        form_layout.addRow("", test_button)
        
        layout.addLayout(form_layout)
        
        # Custom character mapping
        mapping_title = QLabel("<b>Current Character Mapping:</b>")
        layout.addWidget(mapping_title)
        
        self.mapping_label = QLabel()
        self.update_mapping_display()
        layout.addWidget(self.mapping_label)
        
        # Add ACT config reference for the most common configuration
        act_config_label = QLabel(
            "<b>ACT Configuration Reference:</b><br>" +
            "In ACT config files, the mangling is typically set with:<br>" +
            "<code>begin act<br>" +
            "string mangle_chars \".:()<>[],{}\\\"\"\n" +
            "string mangle_letter \"_\"<br>" +
            "end</code>"
        )
        act_config_label.setTextFormat(Qt.RichText)
        layout.addWidget(act_config_label)
        
        # Add buttons
        button_box = QHBoxLayout()
        
        # Reset to defaults button
        reset_button = QPushButton("Reset to Defaults")
        reset_button.clicked.connect(self.reset_to_defaults)
        button_box.addWidget(reset_button)
        
        # Try alternative setting for your problem
        alt_button = QPushButton("Try Alternative Map")
        alt_button.clicked.connect(self.try_alternative_map)
        alt_button.setToolTip("Try an alternative mapping that might help with your issue")
        button_box.addWidget(alt_button)
        
        button_box.addStretch(1)
        
        # OK and Cancel buttons
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        
        button_box.addWidget(ok_button)
        button_box.addWidget(cancel_button)
        
        layout.addLayout(button_box)
        
        # Connect signals to update mapping display
        self.mangle_chars_input.textChanged.connect(self.update_mapping_display)
    
    def update_mapping_display(self):
        """Update the display of the character mapping"""
        mangle_chars = self.mangle_chars_input.text()
        mangle_letter = self.mangle_letter_input.text() if self.mangle_letter_input.text() else "_"
        
        mapping_text = "<table border='0' cellspacing='5'>"
        mapping_text += "<tr><th>Mangled</th><th>Original</th></tr>"
        
        # Add row for the mangle letter itself
        mapping_text += f"<tr><td>{mangle_letter}{mangle_letter}</td><td>{mangle_letter}</td></tr>"
        
        # Add rows for each character in mangle_chars
        for i, char in enumerate(mangle_chars):
            if i < 10:
                # Digits 0-9
                mapping_text += f"<tr><td>{mangle_letter}{i}</td><td>{char}</td></tr>"
            else:
                # Letters a-z
                letter = chr(ord('a') + i - 10)
                mapping_text += f"<tr><td>{mangle_letter}{letter}</td><td>{char}</td></tr>"
                
            # Only show the first 20 mappings to keep it manageable
            if i >= 19:
                mapping_text += "<tr><td colspan='2'>...</td></tr>"
                break
                
        mapping_text += "</table>"
        self.mapping_label.setText(mapping_text)
        self.mapping_label.setTextFormat(Qt.RichText)
        
    def test_unmangling(self):
        """Test unmangling a specific signal"""
        signal = self.test_signal_input.text()
        if not signal:
            return
            
        # Get current settings
        mangle_chars = self.mangle_chars_input.text()
        mangle_letter = self.mangle_letter_input.text() if self.mangle_letter_input.text() else "_"
        
        # Debug this specific signal with current settings
        result = debug_specific_signal(signal, mangle_chars, mangle_letter)
        
        # Show result in a message box
        QMessageBox.information(self, "Unmangling Result", 
                               f"Original: {signal}\nUnmangled: {result}\n\n"
                               f"See console for detailed debug information.")
    
    def reset_to_defaults(self):
        """Reset to default mangling settings"""
        self.mangle_chars_input.setText(":()<>[],{}.\"$\\")
        self.mangle_letter_input.setText("_")
        self.debug_checkbox.setChecked(False)
        
    def try_alternative_map(self):
        """Try an alternative mapping that might work for your specific case"""
        # This has '>' at position 5 which matches what you're seeing
        self.mangle_chars_input.setText(".:()<>[]!@#$%^&*+-=")
        self.update_mapping_display()
        
    def get_mangling_settings(self):
        """Get the mangling settings from the dialog"""
        return {
            'mangle_chars': self.mangle_chars_input.text(),
            'mangle_letter': self.mangle_letter_input.text() if self.mangle_letter_input.text() else "_",
            'debug_mode': self.debug_checkbox.isChecked()
        }


class MainWindow(QMainWindow):
    """Main application window"""
    def __init__(self):
        super().__init__()
        
        # Set window properties
        self.setWindowTitle("Improved Waveform Viewer - Optimized for Large Files")
        self.setGeometry(100, 100, 1200, 800)  # Larger default size
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Create main layout
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(2, 2, 2, 2)  # Minimum margin
        main_layout.setSpacing(2)  # Minimum spacing
        
        # Create left panel for signal selection
        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 0, 0)
        
        # Create signal selector
        signals_label = QLabel("Available Signals:")
        signals_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        signals_label.setFont(QFont("Arial", 10, QFont.Bold))
        left_panel.addWidget(signals_label)
        
        self.signal_selector = SignalSelector(self)
        self.signal_selector.setMinimumWidth(200)  # Set minimum width
        self.signal_selector.setMaximumWidth(300)  # Set maximum width
        self.signal_selector.signal_selection_changed.connect(self.update_plot)
        left_panel.addWidget(self.signal_selector, 1)  # Give it most of the space
        
        # Create signal selection buttons
        signal_buttons_layout = QHBoxLayout()
        
        plot_button = QPushButton("Plot Selected")
        plot_button.clicked.connect(self.update_plot)
        signal_buttons_layout.addWidget(plot_button)
        
        clear_button = QPushButton("Clear Plot")
        clear_button.clicked.connect(self.signal_selector.clear_selection)
        signal_buttons_layout.addWidget(clear_button)
        
        # Add a second row of buttons for select all/none
        select_buttons_layout = QHBoxLayout()
        
        select_all_button = QPushButton("Select All")
        select_all_button.clicked.connect(self.signal_selector.select_all)
        select_buttons_layout.addWidget(select_all_button)
        
        select_filtered_button = QPushButton("Select Filtered")
        select_filtered_button.setToolTip("Select all signals that match the current search")
        select_filtered_button.clicked.connect(self.signal_selector.select_all_filtered)
        select_buttons_layout.addWidget(select_filtered_button)
        
        deselect_all_button = QPushButton("Deselect All")
        deselect_all_button.clicked.connect(self.signal_selector.deselect_all)
        select_buttons_layout.addWidget(deselect_all_button)
        
        # Add both button layouts
        left_panel.addLayout(signal_buttons_layout)
        left_panel.addLayout(select_buttons_layout)
        
        # Create tabbed plot area
        self.plot_tabs = QTabWidget()
        self.plot_tabs.setTabsClosable(True)
        self.plot_tabs.tabCloseRequested.connect(self.close_tab)
        
        # Add to main layout
        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(self.plot_tabs, 3)  # Give plot area more space
        
        # Create toolbar with common actions
        self.create_toolbar()
        
        # Create status bar with progress bar
        self.create_status_bar()
        
        # Add first plot tab
        self.add_plot_tab()
        
        # Initialize file loading variables
        self.current_file = None
        self.file_loader_thread = None
        self.sample_factor = 1
        self.max_memory_mb = 1000
        
        # Monte Carlo simulation limit - similar to xyceWaveformViewer.py
        self.mc_max_simulations = 10
        
        # Hide Monte Carlo button initially
        self.mc_button.setVisible(False)
        
        # Memory monitoring timer
        self.memory_timer = QTimer()
        self.memory_timer.timeout.connect(self.update_memory_display)
        self.memory_timer.start(2000)  # Update every 2 seconds
        
        # Process command line arguments
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self.load_prn_file(sys.argv[1])
            
    def create_toolbar(self):
        """Create toolbar with common actions"""
        # Create toolbar
        toolbar = self.addToolBar("Main Controls")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(24, 24))
        
        # Open file action
        open_action = QAction("Open PRN File", self)
        open_action.triggered.connect(self.open_file_dialog)
        toolbar.addAction(open_action)
        
        # File loading options
        self.file_options_button = QToolButton(self)
        self.file_options_button.setText("File Options")
        self.file_options_button.setPopupMode(QToolButton.InstantPopup)
        
        file_menu = QMenu(self)
        
        # Memory limit setting
        memory_action = QAction("Set Memory Limit...", self)
        memory_action.triggered.connect(self.set_memory_limit)
        file_menu.addAction(memory_action)
        
        # Sampling factor setting
        sample_action = QAction("Set Sampling Factor...", self)
        sample_action.triggered.connect(self.set_sampling_factor)
        file_menu.addAction(sample_action)
        
        # Force garbage collection
        gc_action = QAction("Free Memory (GC)", self)
        gc_action.triggered.connect(self.force_garbage_collection)
        file_menu.addAction(gc_action)
        
        self.file_options_button.setMenu(file_menu)
        toolbar.addWidget(self.file_options_button)
        
        toolbar.addSeparator()
        
        # Add plot tab action
        tab_action = QAction("New Plot Tab", self)
        tab_action.triggered.connect(self.add_plot_tab)
        toolbar.addAction(tab_action)
        
        # Export plot action
        export_action = QAction("Export Plot", self)
        export_action.triggered.connect(self.export_plot)
        toolbar.addAction(export_action)
        
        toolbar.addSeparator()
        
        # Configure mangling settings
        mangling_action = QAction("Configure ACT Mangling", self)
        mangling_action.triggered.connect(self.configure_mangling)
        toolbar.addAction(mangling_action)
        
        # Monte Carlo options - simplified like xyceWaveformViewer.py
        self.mc_button = QToolButton(self)
        self.mc_button.setText("Monte Carlo Options")
        self.mc_button.setPopupMode(QToolButton.InstantPopup)
        
        mc_menu = QMenu(self)
        
        # Set simulation limit
        limit_action = QAction("Set Simulation Limit...", self)
        limit_action.triggered.connect(self.set_monte_carlo_limit)
        mc_menu.addAction(limit_action)
        
        self.mc_button.setMenu(mc_menu)
        toolbar.addWidget(self.mc_button)
        
    def create_status_bar(self):
        """Create status bar with progress bar and memory display"""
        self.statusBar()
        
        # Progress bar for file loading
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumWidth(200)
        self.statusBar().addPermanentWidget(self.progress_bar)
        
        # Memory usage display
        self.memory_label = QLabel("Memory: 0 MB")
        self.memory_label.setMinimumWidth(100)
        self.statusBar().addPermanentWidget(self.memory_label)
        
        # Cancel button for file loading
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.setMaximumWidth(60)
        self.cancel_button.clicked.connect(self.cancel_file_loading)
        self.statusBar().addPermanentWidget(self.cancel_button)
        
    def update_memory_display(self):
        """Update memory usage display"""
        memory_mb = get_memory_usage()
        self.memory_label.setText(f"Memory: {memory_mb:.1f} MB")
        
        # Change color based on memory usage
        if memory_mb > self.max_memory_mb * 0.8:
            self.memory_label.setStyleSheet("color: red; font-weight: bold;")
        elif memory_mb > self.max_memory_mb * 0.6:
            self.memory_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            self.memory_label.setStyleSheet("color: green;")
            
    def set_memory_limit(self):
        """Set memory limit for file loading"""
        limit, ok = QInputDialog.getInt(
            self, "Memory Limit", 
            "Maximum memory to use for file loading (MB):", 
            value=self.max_memory_mb, min=100, max=8000
        )
        
        if ok:
            self.max_memory_mb = limit
            
    def set_sampling_factor(self):
        """Set sampling factor for large files"""
        factor, ok = QInputDialog.getInt(
            self, "Sampling Factor", 
            "Sample every Nth data point (1 = no sampling, 2 = every other point, etc.):", 
            value=self.sample_factor, min=1, max=1000
        )
        
        if ok:
            self.sample_factor = factor
            
    def force_garbage_collection(self):
        """Force garbage collection to free memory"""
        initial_memory = get_memory_usage()
        gc.collect()
        final_memory = get_memory_usage()
        freed = initial_memory - final_memory
        
        self.statusBar().showMessage(f"Freed {freed:.1f} MB of memory", 3000)
        
    def cancel_file_loading(self):
        """Cancel file loading operation"""
        if self.file_loader_thread and self.file_loader_thread.isRunning():
            self.file_loader_thread.stop_loading()
            self.progress_bar.setVisible(False)
            self.cancel_button.setVisible(False)
            self.statusBar().showMessage("File loading cancelled", 3000)
        
    def toggle_monte_carlo_all(self, checked):
        """Toggle showing all Monte Carlo simulations"""
        self.update_plot()
            
    def toggle_monte_carlo_avg(self, checked):
        """Toggle showing Monte Carlo average"""
        self.update_plot()
            
    def open_file_dialog(self):
        """Open file dialog to select PRN file"""
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open PRN File", "", "PRN Files (*.prn);;All Files (*)", options=options
        )
        
        if file_path:
            self.load_prn_file(file_path)
            
    def load_prn_file(self, file_path):
        """Load PRN file data using background thread"""
        # Check if a file is already being loaded
        if self.file_loader_thread and self.file_loader_thread.isRunning():
            reply = QMessageBox.question(
                self, "File Loading", 
                "Another file is currently being loaded. Cancel it and load this file?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.cancel_file_loading()
            else:
                return
        
        # Check file size and warn for very large files
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        
        if file_size_mb > 100:  # Warn for files over 100MB
            reply = QMessageBox.question(
                self, "Large File Warning", 
                f"This file is {file_size_mb:.1f} MB. Loading may take time and use significant memory.\n\n"
                f"Current settings:\n"
                f"• Memory limit: {self.max_memory_mb} MB\n"
                f"• Sampling factor: {self.sample_factor} (1 = no sampling)\n\n"
                f"You can adjust these settings in File Options menu.\n\n"
                f"Continue loading?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.No:
                return
        
        # Show progress bar and cancel button
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.cancel_button.setVisible(True)
        
        # Create and start the file loader thread
        self.file_loader_thread = FileLoaderThread(
            file_path, 
            sample_factor=self.sample_factor,
            max_memory_mb=self.max_memory_mb
        )
        
        # Connect signals
        self.file_loader_thread.progress_update.connect(self.on_loading_progress)
        self.file_loader_thread.data_loaded.connect(self.on_data_loaded)
        self.file_loader_thread.error_occurred.connect(self.on_loading_error)
        self.file_loader_thread.finished.connect(self.on_loading_finished)
        
        # Start loading
        self.file_loader_thread.start()
        
    def on_loading_progress(self, percentage, message):
        """Handle progress updates from file loading thread"""
        self.progress_bar.setValue(percentage)
        self.statusBar().showMessage(message)
        
    def on_data_loaded(self, df, file_path):
        """Handle successful data loading"""
        try:
            # Store data in global dictionary
            global df_dict
            df_dict[file_path] = df
            
            # Set current file
            self.current_file = file_path
            
            # Check if this is Monte Carlo data
            is_monte_carlo = False
            if len(df) > 0:
                first_col = df.columns[0]
                if isinstance(df[first_col].iloc[0], (np.ndarray, list)):
                    is_monte_carlo = True
                    self.mc_button.setVisible(True)
                    
                    # Count simulations
                    num_sims = len(df[first_col])
                    QMessageBox.information(
                        self, "Monte Carlo Data", 
                        f"Detected Monte Carlo data with {num_sims} simulations"
                    )
                else:
                    self.mc_button.setVisible(False)
            
            # Update signal selector
            self.update_signal_selector()
            
            # Update window title
            base_name = os.path.basename(file_path)
            if self.sample_factor > 1:
                self.setWindowTitle(f"Improved Waveform Viewer - {base_name} (sampled 1:{self.sample_factor})")
            else:
                self.setWindowTitle(f"Improved Waveform Viewer - {base_name}")
            
            # Show success message in status bar
            if is_monte_carlo:
                self.statusBar().showMessage(f"Loaded {base_name} with Monte Carlo data", 5000)
            else:
                self.statusBar().showMessage(f"Loaded {base_name} with {len(df)} data points", 5000)
                
        except Exception as e:
            self.statusBar().showMessage(f"Error processing loaded data: {str(e)}", 5000)
            
    def on_loading_error(self, error_message):
        """Handle loading errors"""
        QMessageBox.critical(self, "File Loading Error", error_message)
        self.statusBar().showMessage("File loading failed", 5000)
        
    def on_loading_finished(self):
        """Handle loading thread completion"""
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.file_loader_thread = None
            
    def update_signal_selector(self):
        """Update the signal selector with available signals"""
        if not self.current_file or self.current_file not in df_dict:
            return
            
        # Get data
        df = df_dict[self.current_file]
        
        # Get signal names - use xyceWaveformViewer approach
        signal_names = []
        
        # For Monte Carlo data
        is_monte_carlo = False
        if len(df) > 0:
            first_col = df.columns[0]
            if isinstance(df[first_col].iloc[0], np.ndarray) or isinstance(df[first_col].iloc[0], list):
                is_monte_carlo = True
        
        if is_monte_carlo:
            # All column names except first two columns (index and time)
            if len(df.columns) >= 2:
                signal_names = list(df.columns[2:])  # Skip first two columns (index and time)
        else:
            # For regular data, also skip first two columns (index and time)
            if len(df.columns) >= 2:
                signal_names = list(df.columns[2:])  # Skip first two columns (index and time)
        
        # Add signals to selector widget
        self.signal_selector.add_signals(signal_names)
    
    def update_plot(self):
        """Update plot with selected signals"""
        if not self.current_file or self.current_file not in df_dict:
            return
            
        # Get current plot tab
        current_tab = self.plot_tabs.currentWidget()
        if not current_tab:
            return
            
        # Get active canvas from the tab
        canvas = current_tab.get_active_canvas()
        if not canvas:
            return
            
        # Get selected signals (these are original mangled names)
        selected_signals = self.signal_selector.get_selected_signals()
        if not selected_signals:
            canvas.clear_plot()
            # Clear legend panel too
            if hasattr(current_tab, 'update_legend_panel'):
                current_tab.update_legend_panel([], [], [])
            return
            
        # For legend display, we want to show unmangled names if option is selected
        display_names = []
        if hasattr(self.signal_selector, 'unmangle_checkbox') and self.signal_selector.unmangle_checkbox.isChecked():
            convert_to_act = hasattr(self.signal_selector, 'act_style_checkbox') and self.signal_selector.act_style_checkbox.isChecked()
            for signal in selected_signals:
                display_names.append(unmangle_signal_name(signal, convert_to_act_style=convert_to_act))
        else:
            display_names = selected_signals.copy()
        
        try:    
            # Get data
            df = df_dict[self.current_file]
            
            # Check if this is Monte Carlo data
            is_monte_carlo = False
            if len(df) > 0:
                first_col = df.columns[0]
                if isinstance(df[first_col].iloc[0], np.ndarray) or isinstance(df[first_col].iloc[0], list):
                    is_monte_carlo = True
            
            if is_monte_carlo:
                # For Monte Carlo, we need special handling
                self.plot_monte_carlo_data(current_tab, canvas, df, selected_signals, display_names)
            else:
                # Regular data plotting
                self.plot_regular_data(current_tab, canvas, df, selected_signals, display_names)
        except Exception as e:
            # Show error message
            error_msg = f"Error plotting data: {str(e)}"
            self.statusBar().showMessage(error_msg, 5000)
            if DEBUG_MODE:
                import traceback
                traceback.print_exc()
    
    def plot_monte_carlo_data(self, tab, canvas, df, selected_signals, display_names=None):
        """Plot Monte Carlo simulation data"""
        # Use display_names if provided, otherwise use selected_signals
        if display_names is None:
            display_names = selected_signals
            
        # Use xyceWaveformViewer approach - always use second column as time
        if len(df.columns) < 2:
            self.statusBar().showMessage("Data file does not have enough columns", 3000)
            return
            
        time_column = df.columns[1]  # Always use second column as time
        
        # Prepare arrays for plotting
        all_time_arrays = []
        all_signal_arrays = []
        all_labels = []
        
        # Get the number of Monte Carlo simulations
        if len(df) > 0 and isinstance(df[time_column].iloc[0], (np.ndarray, list)):
            num_simulations = len(df[time_column])
        else:
            self.statusBar().showMessage("Invalid Monte Carlo data format", 3000)
            return
            
        # Limit number of simulations to display based on user setting
        max_sims = min(self.mc_max_simulations, num_simulations)
            
        # For each selected signal, collect data from all simulations
        for i, signal in enumerate(selected_signals):
            if signal not in df.columns:
                continue
                
            # Get display name for this signal
            display_name = display_names[i] if i < len(display_names) else signal
                
            for j in range(max_sims):
                try:
                    # Get time and signal data for this simulation
                    time_array = np.array(df[time_column].iloc[j]).astype(float)
                    signal_array = np.array(df[signal].iloc[j]).astype(float)
                    
                    # Skip if arrays are different lengths
                    if len(time_array) != len(signal_array) or len(time_array) == 0:
                        continue
                    
                    all_time_arrays.append(time_array)
                    all_signal_arrays.append(signal_array)
                    all_labels.append(f"{display_name} (Sim {j+1})")
                except Exception as e:
                    if DEBUG_MODE:
                        print(f"Error processing Monte Carlo data: {e}")
                    continue
        
        # Plot signals if we have valid data
        if all_time_arrays and all_signal_arrays:
            canvas.plot_signals(all_time_arrays, all_signal_arrays, all_labels, is_monte_carlo=True)
    
    def plot_regular_data(self, tab, canvas, df, selected_signals, display_names=None):
        """Plot regular (non-Monte Carlo) data"""
        # Use display_names if provided, otherwise use selected_signals
        if display_names is None:
            display_names = selected_signals
            
        # Use xyceWaveformViewer approach - always use second column as time
        if len(df.columns) < 2:
            self.statusBar().showMessage("Data file does not have enough columns", 3000)
            return
            
        time_column = df.columns[1]  # Always use second column as time
        
        # Prepare data for plotting
        time_data = df[time_column].values
        all_time_arrays = []
        all_signal_arrays = []
        all_labels = []
        
        for i, signal in enumerate(selected_signals):
            # Get display name for this signal
            display_name = display_names[i] if i < len(display_names) else signal
                
            if signal in df.columns:
                all_time_arrays.append(time_data)
                all_signal_arrays.append(df[signal].values)
                all_labels.append(display_name)
            else:
                # If signal name not found exactly, try to match
                matched_col = None
                for col in df.columns:
                    if signal in col:
                        matched_col = col
                        break
                
                if matched_col:
                    all_time_arrays.append(time_data)
                    all_signal_arrays.append(df[matched_col].values)
                    all_labels.append(display_name)  # Use display name for the matched column
                else:
                    self.statusBar().showMessage(f"Signal '{signal}' not found in data", 3000)
                    continue
        
        # Plot signals if we have valid data
        if all_time_arrays and all_signal_arrays:
            canvas.plot_signals(all_time_arrays, all_signal_arrays, all_labels)
    
    def add_plot_tab(self):
        """Add new plot tab"""
        new_tab = PlotTab()
        tab_count = self.plot_tabs.count()
        self.plot_tabs.addTab(new_tab, f"Plot {tab_count + 1}")
        self.plot_tabs.setCurrentIndex(tab_count)
        
    def close_tab(self, index):
        """Close plot tab"""
        if self.plot_tabs.count() > 1:  # Keep at least one tab
            self.plot_tabs.removeTab(index)
            
    def export_plot(self):
        """Export current plot as image"""
        if not self.plot_tabs.count():
            return
            
        current_tab = self.plot_tabs.currentWidget()
        
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Plot", "", "PNG Files (*.png);;PDF Files (*.pdf);;All Files (*)", options=options
        )
        
        if file_path:
            try:
                current_tab.canvas.fig.savefig(file_path, dpi=300, bbox_inches='tight')
                QMessageBox.information(self, "Export Successful", f"Plot saved to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"Failed to save plot: {str(e)}")

    def set_monte_carlo_limit(self):
        """Set limit for number of Monte Carlo simulations to display"""
        limit, ok = QInputDialog.getInt(
            self, "Monte Carlo Limit", 
            "Maximum number of Monte Carlo simulations to display:", 
            value=self.mc_max_simulations, min=1, max=100
        )
        
        if ok:
            self.mc_max_simulations = limit
            # Update plot if we have Monte Carlo data loaded
            if self.current_file and self.current_file in df_dict:
                df = df_dict[self.current_file]
                if len(df) > 0:
                    first_col = df.columns[0]
                    if isinstance(df[first_col].iloc[0], np.ndarray) or isinstance(df[first_col].iloc[0], list):
                        self.update_plot()

    def configure_mangling(self):
        """Show dialog to configure ACT name mangling settings"""
        dialog = ManglingConfigDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            # Update global mangling settings
            settings = dialog.get_mangling_settings()
            
            global MANGLE_CHARS, MANGLE_LETTER, DEBUG_MODE
            MANGLE_CHARS = settings['mangle_chars']
            MANGLE_LETTER = settings['mangle_letter']
            DEBUG_MODE = settings['debug_mode']
            
            # Refresh signal display if signals are loaded and unmangle is enabled
            if hasattr(self, 'signal_selector') and hasattr(self.signal_selector, 'unmangle_checkbox'):
                if self.signal_selector.unmangle_checkbox.isChecked():
                    self.signal_selector.refresh_signal_names()
                    
                    # Update plot with new unmangled names
                    self.update_plot()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main() 
