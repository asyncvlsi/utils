# Generates spice simulation for a component with specified netlist
import subprocess
import os, sys, shutil
from os import path

# Create the simulation deck
class sim_deck():
    def __init__(self, MODEL, TOP, NAME, PINS, ACT):
        self.model = MODEL
        self.process = "tt"
        self.vdd_name = "Vdd"
        self.gnd_name = "GND"
        self.voltage = 1
        self.top = TOP
        self.name = "mem::{0}".format(NAME)
        self.dut_pins = PINS
        self.tmp_dir = "test_{0}".format(self.top)
        self.act_file = ACT
        # Creating test directory, will populate all generated files in here
        if path.exists(self.tmp_dir):
            delete = input("Directory already exists! Enter y to empty directory {0} ".format(self.tmp_dir))
            if(delete == "y"):
                cmd = "rm -r {}/".format(self.tmp_dir)
                subprocess.call(cmd, shell=True)
        
        if not path.exists(self.tmp_dir): 
            subprocess.call("mkdir {}".format(self.tmp_dir), shell=True)
            
        self.dut = open("{0}/dut.sp".format(self.tmp_dir), "w")
        self.deck = open("{0}/{1}_test.sp".format(self.tmp_dir, self.top), "w")
        self.create_deck()
    
    # Instantiate the dut subckt
    def dut_inst(self):
        self.deck.write("* DUT instance")
        self.dut.write("X{0} ".format(self.top))
        for pin in self.dut_pins:
            self.dut.write("{0} ".format(pin))
        self.dut.write("{0}\n".format(self.name))
        
    # Create dut netlist
    def dut_net(self):
        self.dut.write("\n")
        self.dut.write(".inc new_{0}.sp\n".format(self.top))
        self.dut_inst()
        self.dut.close()
        
    # Populate Spice simulation file
    def spice_deck(self):
        self.deck.write("* SIMULATION DECK\n\n")
        self.deck.write(".global {0} {1}\n".format(self.vdd_name, self.gnd_name))
        self.deck.write(".option finesim_ichier=global\n")
        self.deck.write("vpwr0 {0} 0 dc {1}v\n".format(self.vdd_name, self.voltage))
        self.deck.write("vpwr1 {0} 0 dc {1}v\n\n".format(self.gnd_name, 0))
        self.deck.write(".lib {0} {1}\n".format(self.model, self.process))
        self.deck.write(".inc new_dut.sp\n\n")
        self.deck.write(".option post\n")
        self.deck.write(".probe v(*)\n")
        self.deck.write(".option finesim_measout=2\n")
        self.deck.write(".option finesim_mode=spicemd\n")
        self.deck.write(".print in(V({0}))\n".format(self.vdd_name))
        self.deck.write(".print in(V({0}))\n\n".format(self.gnd_name))
        
        
    # Add pulse    
    def add_pulse(self, V_name, N1, N2, v_l, v_h, td, tr, tf, pw, per, t_tot):
        pulse_str = "{0} {1} {2} PULSE "
        self.deck.write(pulse_str.format(V_name, N1, N2))
        self.deck.write("{0} {1} {2} {3} {4} {5} {6} {7}".format(v_l, v_h, td, tr, tf,pw, per, t_tot))
        self.deck.write("\n")
        
    # Add ic
    def add_ic_v(self, N, v_0):
        ic_str = ".ic V({0})={1}\n".format(N, v_0)
        self.deck.write(ic_str)
        self.deck.write("\n")
    # Add DC voltage source
    def add_dc_v(self,v_name, N1, N2, v_0):
        ic_str = "{0} {1} {2} {3}V\n".format(v_name, N1, N2, v_0)
        self.deck.write(ic_str)
        self.deck.write("\n")
        
    # Add piecewise linear voltage function
    def add_pwl(self, V_name, N1, N2, node_voltage_pairs):
        pwl_str = "{0} {1} {2} pwl"
        self.deck.write(pwl_str.format(V_name, N1, N2))
        for pair in node_voltage_pairs:
            self.deck.write(" {0}ns {1}V".format(pair[0], pair[1]))
        self.deck.write("\n")
    
    # Add dc analysis, close file
    def add_dc_analysis(self, N, V_1, V_2, dV):
        self.deck.write(".dc {0} {1} {2} {3}\n".format(N, V_1, V_2, dV))
        self.deck.write(".end\n")
        self.deck.close()
        
    # Add transient analysis           
    def add_tran_analysis(self, dt, t_f):
        self.deck.write(".tran {0} {1} UIC\n".format(dt, t_f))
        self.deck.write(".end\n")
        self.deck.close()
        
    
    # Remove ::mem::, <, >, and comma from model names in subcircuit
    def edit_netlist(self):
        fin= open("{0}/{1}.sp".format(self.tmp_dir, self.top), "r")
        fout = open("{0}/new_{1}.sp".format(self.tmp_dir, self.top), 'w')

        excludedWord = ["::mem::", "<", ">", ","]
        for line in fin:
            for word in excludedWord:
                line = line.replace(word, '')
            fout.write(line)
        fin.close()
        fout.close()
        
    # Creates new spice file with ::mem::, <, >, and comma removed from dut
    def edit_dut_netlist(self):
        fin= open("{0}/dut.sp".format(self.tmp_dir), "r")
        fout = open("{0}/new_dut.sp".format(self.tmp_dir), 'w')

        excludedWord = ["::mem::", "mem::", "<", ">", ","]
        for line in fin:
            for word in excludedWord:
                line = line.replace(word, '')
            fout.write(line)
        fin.close()
        fout.close()
    
    # Append process to act file, create netlist with sky130 config
    def edit_act(self):
		# Creating a new directory for the actfile with the added model and prs2net 
        if path.exists("new_prs_{0}".format(self.tmp_dir)):
            delete = input("Directory already exists! Enter y to empty directory {0} ".format("new_prs_{0}".format(self.tmp_dir)))
            if(delete == "y"):
                cmd = "rm -r {}/".format("new_prs_{0}".format(self.tmp_dir))
                subprocess.call(cmd, shell=True)
        if not path.exists("new_prs_{0}".format(self.tmp_dir)):
            subprocess.call("mkdir {}".format("new_prs_{0}".format(self.tmp_dir)), shell=True)
        
        shutil.copyfile('{0}'.format(self.act_file), 'new_{0}.act'.format(self.top))
        fin= open("new_{0}.act".format(self.top), "a")
        fin.write("{0} {1}x;\n".format(self.name, self.top))
        fin.close()

        cmd1 = "$ACT_HOME/bin/aflat new_{0}.act > new_{0}.prs".format(self.top)
        cmd2 = "$ACT_HOME/bin/prs2net -ref=1 -Tsky130l -p '{0}' -o {1}.sp new_{1}.act".format(self.name, self.top)
        cmd3 = "mv {0}.sp {1}".format(self.top, self.tmp_dir)
        cmd4 = "mv new_{0}.act {1}".format(self.top, "new_prs_{0}".format(self.tmp_dir))
        cmd5 = "mv new_{0}.prs {1}".format(self.top, "new_prs_{0}".format(self.tmp_dir))
        
        
        subprocess.call(cmd1, shell=True)
        subprocess.call(cmd2, shell=True)
        subprocess.call(cmd3, shell=True)
        subprocess.call(cmd4, shell=True)
        subprocess.call(cmd5, shell=True)
           
    def create_deck(self):
        self.edit_act()
        self.dut_net()
        self.spice_deck()
        self.edit_netlist()
        self.edit_dut_netlist()
        
    