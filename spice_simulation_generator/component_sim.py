# Creates spice deck to simulate a particular component

import spice_gen

# Top level 
TOP = "bitcell_array"

# Name of component in act file
NAME = "bitcell_array<1,1>"

# ACT file where everything is located (same directory)
ACT = "bitcell.act"

# IO pins for module
PINS = ["wl",  "bl", "bl_"]
#PINS = ["din[0]", "din[1]", "wl[0]", "wl[1]", "go", "sen", "wen", "pchg", "wreq", "Reset", "en1_s", "en2_s", "en1_m", "en2_m", "dout[0]", "dout[1]", "dr", "wc"]

# Model file
MODEL = "/gpfs/gibbs/pi/manohar/tech/SKY/130/skywater-pdk/libraries/sky130_fd_pr/latest/models/sky130.lib.spice"

deck = spice_gen.sim_deck(MODEL, TOP, NAME, PINS, ACT)


#could be replaced with for loops in larger arrays
#no stimulus to output pins
#writing data to the first row of the bank
deck.add_ic_v("Q", 1)

for i in range(len(PINS)): 
	pin = PINS[i]
	deck.add_pulse("V{0}".format(pin), "{0}".format(pin), "GND", 0, 1, "5ns", "1ps", "1ps", "5ns", "30ns", "30ns")
	
deck.add_tran_analysis(".1ns", "30ns")
