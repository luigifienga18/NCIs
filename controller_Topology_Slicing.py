# topology_slicing.py
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3

class TopologySliceController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TopologySliceController, self).__init__(*args, **kwargs)
        
        # MAC Address degli Host come definito nel progetto
        self.H = {
            'h1': '00:00:00:00:00:01',
            'h2': '00:00:00:00:00:02',
            'h3': '00:00:00:00:00:03',
            'h4': '00:00:00:00:00:04'
        }

        # MAPPATURA PORTE (Topology Map)
        # Questa mappa riflette la topologia della rete
        self.PORT_MAP = {
            # s1: 1->h1, 2->h2, 3->s2 (Upper), 4->s3 (Lower)
            1: {'h1': 1, 'h2': 2, 's2': 3, 's3': 4},
            # s2: 1->s1, 2->s4
            2: {'s1': 1, 's4': 2},
            # s3: 1->s1, 2->s4
            3: {'s1': 1, 's4': 2},
            # s4: 1->s2 (Upper), 2->s3 (Lower), 3->h3, 4->h4
            4: {'s2': 1, 's3': 2, 'h3': 3, 'h4': 4}
        }

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        parser = dp.ofproto_parser
        
        # --- 0. REGOLA DI DEFAULT (DROP) ---
        # Blocca tutto ciò che non è esplicitamente permesso. 
        # Fondamentale per l'isolamento delle slice.
        match = parser.OFPMatch()
        self.add_flow(dp, 0, match, [])

        # --- 1. GESTIONE ARP (Priorità 100) ---
        # L'ARP deve essere instradato SOLO all'interno della propria slice
        # per permettere il ping h1-h3 e h2-h4 senza "leakare" nell'altra slice.

        if dpid == 1: # SWITCH S1 (Ingresso)
            # Upper Slice (ARP): h1 (port 1) -> s2 (port 3)
            match = parser.OFPMatch(in_port=1, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['s2'])]
            self.add_flow(dp, 100, match, actions)

            # Lower Slice (ARP): h2 (port 2) -> s3 (port 4)
            match = parser.OFPMatch(in_port=2, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['s3'])]
            self.add_flow(dp, 100, match, actions)

            # Ritorno ARP Upper: da s2 (port 3) -> h1
            match = parser.OFPMatch(in_port=3, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h1'])]
            self.add_flow(dp, 100, match, actions)

            # Ritorno ARP Lower: da s3 (port 4) -> h2
            match = parser.OFPMatch(in_port=4, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h2'])]
            self.add_flow(dp, 100, match, actions)

        elif dpid == 2: # SWITCH S2 (Upper Transit)
            # Forwarding ARP semplice tra le due porte (Tunnel Upper)
            match = parser.OFPMatch(eth_type=0x0806)
            actions = [parser.OFPActionOutput(ofproto_v1_3.OFPP_FLOOD)]
            self.add_flow(dp, 100, match, actions)

        elif dpid == 3: # SWITCH S3 (Lower Transit)
            # Forwarding ARP semplice tra le due porte (Tunnel Lower)
            match = parser.OFPMatch(eth_type=0x0806)
            actions = [parser.OFPActionOutput(ofproto_v1_3.OFPP_FLOOD)]
            self.add_flow(dp, 100, match, actions)

        elif dpid == 4: # SWITCH S4 (Uscita)
            # Upper Slice (ARP): da s2 (port 1) -> h3 (port 3)
            match = parser.OFPMatch(in_port=1, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h3'])]
            self.add_flow(dp, 100, match, actions)

            # Lower Slice (ARP): da s3 (port 2) -> h4 (port 4)
            match = parser.OFPMatch(in_port=2, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h4'])]
            self.add_flow(dp, 100, match, actions)
            
            # ARP Reply da h3 -> s2
            match = parser.OFPMatch(in_port=3, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['s2'])]
            self.add_flow(dp, 100, match, actions)

            # ARP Reply da h4 -> s3
            match = parser.OFPMatch(in_port=4, eth_type=0x0806)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['s3'])]
            self.add_flow(dp, 100, match, actions)

        # --- 2. GESTIONE TRAFFICO IP/DATI (Priorità 200) ---
        # Implementazione delle slice statiche basate su MAC address Src/Dst
        
        # === SWITCH S1 ===
        if dpid == 1:
            # UPPER SLICE: H1 -> H3 (via S2)
            match = parser.OFPMatch(eth_src=self.H['h1'], eth_dst=self.H['h3'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['s2'])]
            self.add_flow(dp, 200, match, actions)

            # LOWER SLICE: H2 -> H4 (via S3)
            match = parser.OFPMatch(eth_src=self.H['h2'], eth_dst=self.H['h4'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['s3'])]
            self.add_flow(dp, 200, match, actions)

            # RITORNO UPPER: H3 -> H1
            match = parser.OFPMatch(eth_src=self.H['h3'], eth_dst=self.H['h1'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h1'])]
            self.add_flow(dp, 200, match, actions)

            # RITORNO LOWER: H4 -> H2
            match = parser.OFPMatch(eth_src=self.H['h4'], eth_dst=self.H['h2'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h2'])]
            self.add_flow(dp, 200, match, actions)

        # === SWITCH S2 (Solo Upper Traffic) ===
        elif dpid == 2:
            # Verso H3 (destra)
            match = parser.OFPMatch(eth_dst=self.H['h3'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[2]['s4'])]
            self.add_flow(dp, 200, match, actions)

            # Verso H1 (sinistra)
            match = parser.OFPMatch(eth_dst=self.H['h1'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[2]['s1'])]
            self.add_flow(dp, 200, match, actions)

        # === SWITCH S3 (Solo Lower Traffic) ===
        elif dpid == 3:
            # Verso H4 (destra)
            match = parser.OFPMatch(eth_dst=self.H['h4'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[3]['s4'])]
            self.add_flow(dp, 200, match, actions)

            # Verso H2 (sinistra)
            match = parser.OFPMatch(eth_dst=self.H['h2'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[3]['s1'])]
            self.add_flow(dp, 200, match, actions)

        # === SWITCH S4 ===
        elif dpid == 4:
            # UPPER SLICE: H1 -> H3
            match = parser.OFPMatch(eth_src=self.H['h1'], eth_dst=self.H['h3'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h3'])]
            self.add_flow(dp, 200, match, actions)

            # LOWER SLICE: H2 -> H4
            match = parser.OFPMatch(eth_src=self.H['h2'], eth_dst=self.H['h4'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h4'])]
            self.add_flow(dp, 200, match, actions)

            # RITORNO UPPER: H3 -> H1 (via S2)
            match = parser.OFPMatch(eth_src=self.H['h3'], eth_dst=self.H['h1'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['s2'])]
            self.add_flow(dp, 200, match, actions)

            # RITORNO LOWER: H4 -> H2 (via S3)
            match = parser.OFPMatch(eth_src=self.H['h4'], eth_dst=self.H['h2'])
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['s3'])]
            self.add_flow(dp, 200, match, actions)