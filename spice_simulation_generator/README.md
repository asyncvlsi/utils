## Python script to automate spice file generation from ACT to netlist

# spice_gen.py
Contains a class called sim_deck which contains all the methods needed to generate a spice file from a given top level process, act file, and model. 

# component_sim.py
component_sim.py is a file that generates a spice file by specifing the correpsonding act file, top level process, and technology model. Creates a test directory which contains the generated spice files. Takes input flags which include a reference, the technology configuration in ACT, and the process corner. An example cli input to generate a spice file in skywater130 in the normal process corner would be: "python3 component_sim.py -r ref=1 -t Tsky130l -p tt". 

This implementation follows a similar approach used by Samira Ataei. 
