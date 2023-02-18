# Utility Scripts

This repository contains a number of useful scripts for working with the ACT flow.


## lib2act.py

This is a quick-and-dirty script that does a very simple line-based
string parsing of a Liberty file to find all the cell names and pin
names. These are then emitted as an ACT file that contains all the
cell definitions specified using the ACT black-box syntax.

If your design instantiates these cells, then ACT will assume they
are specified in some external format (e.g. provided by a 
cell library vendor), and treats them as black boxes in the design.

To use, run `lib2act.py < file.lib > out.act`
