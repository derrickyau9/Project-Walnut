# HOST PIN REFERENCE ONLY. Not a complete, validated FPGA design.
# Source: AMD UG1182 v1.7, Tables 3-44 and 3-45, ZCU102 J5 HPC0.
# Port names below identify carrier DP channels, NOT IMX410 lane ordering.
# Use only after confirming PCB revision, receiver IP port names, GT locations,
# clocking, coupling/termination and all constraints with the actual design.
# Do not apply LVCMOS/LVDS IOSTANDARD properties to these GTH serial pins.
set_property PACKAGE_PIN H2 [get_ports {hpc0_rx_p[0]}]
set_property PACKAGE_PIN H1 [get_ports {hpc0_rx_n[0]}]
set_property PACKAGE_PIN J4 [get_ports {hpc0_rx_p[1]}]
set_property PACKAGE_PIN J3 [get_ports {hpc0_rx_n[1]}]
set_property PACKAGE_PIN F2 [get_ports {hpc0_rx_p[2]}]
set_property PACKAGE_PIN F1 [get_ports {hpc0_rx_n[2]}]
set_property PACKAGE_PIN K2 [get_ports {hpc0_rx_p[3]}]
set_property PACKAGE_PIN K1 [get_ports {hpc0_rx_n[3]}]
set_property PACKAGE_PIN L4 [get_ports {hpc0_rx_p[4]}]
set_property PACKAGE_PIN L3 [get_ports {hpc0_rx_n[4]}]
set_property PACKAGE_PIN P2 [get_ports {hpc0_rx_p[5]}]
set_property PACKAGE_PIN P1 [get_ports {hpc0_rx_n[5]}]
set_property PACKAGE_PIN T2 [get_ports {hpc0_rx_p[6]}]
set_property PACKAGE_PIN T1 [get_ports {hpc0_rx_n[6]}]
set_property PACKAGE_PIN M2 [get_ports {hpc0_rx_p[7]}]
set_property PACKAGE_PIN M1 [get_ports {hpc0_rx_n[7]}]
# No sensor pin/package constraints, GT reference frequency, sensor clock
# electrical standard, bit rate or timing constraints are guessed here.
