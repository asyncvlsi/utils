# Creates spice deck to simulate a bitcell using the spice gen module

import spice_gen

# Top level 
TOP = "bitcell_array"

# Name of component in act file
NAME = "bitcell_array<4,4>"

# ACT file where everything is located (same directory)
ACT = "bitcell.act"

# Model file
MODEL = "$ACT_HOME/$ACT_TECH/models.sp"

# Creating Deck
deck = spice_gen.sim_deck(MODEL, TOP, NAME, ACT)

# Determining pins to dut from spice deck
PINS = deck.input_pins

#could be replaced with for loops in larger arrays
#no stimulus to output pins
#writing data to the first row of the bank
deck.add_ic_v("Q", 1)

for i in range(len(PINS)): 
	pin = PINS[i]
	deck.add_pulse("V{0}".format(pin), "{0}".format(pin), "GND", 0, 1, "5ns", "1ps", "1ps", "5ns", "30ns", "30ns")
	
deck.add_tran_analysis(".1ns", "30ns")
