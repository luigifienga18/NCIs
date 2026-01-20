# NCIs

## SDN Network Slicing & Dynamic Traffic Management

This project implements a virtualized network using Mininet and the Ryu controller (OpenFlow 1.3 protocol), focusing on three types of slicing:

- Topology Slicing: Strict isolation of traffic between specific host pairs (H1-H3 and H2-H4), routed on physically distinct network paths (Upper and Lower slices).

- Service Slicing: Differentiated routing based on traffic type. Video traffic (identified via UDP port 9999) is prioritized on the high-speed slice (10 Mbps), while standard traffic is relegated to the low-speed slice (1 Mbps).

- Dynamic Slicing: Implementation of an active monitoring system that analyzes flow statistics every 2 seconds. If the bandwidth occupied by video is low, standard traffic can occupy the fast slice; in case of video congestion (threshold > 1 Mbps), standard traffic is dynamically moved to the slow slice to ensure Quality of Service (QoS).
