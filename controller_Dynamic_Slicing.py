# controller_Dynamic_Slicing_Bidirectional_FlowStats.py
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

class DynamicSliceController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(DynamicSliceController, self).__init__(*args, **kwargs)
        
        self.datapaths = {}
        
        self.monitor_interval = 2
        self.bandwidth_threshold = 1000000 / 8  # 1 Mbps
        
        # Dizionario per i byte dei flussi VIDEO precedenti
        # Chiave: dpid -> Valore: byte totali video letti prima
        self.video_stats = {
            1: 0,
            4: 0
        }

        self.global_slice_state = 'LOWER'
        self.monitor_thread = hub.spawn(self._monitor)

        self.H = {'h1':'00:00:00:00:00:01', 'h2':'00:00:00:00:00:02', 'h3':'00:00:00:00:00:03', 'h4':'00:00:00:00:00:04'}
        self.PORT_MAP = {
            1: {'h1': 1, 'h2': 2, 's2': 3, 's3': 4},
            2: {'s1': 1, 's4': 2},
            3: {'s1': 1, 's4': 2},
            4: {'s2': 1, 's3': 2, 'h3': 3, 'h4': 4}
        }

    # --- MONITORING LOOP (Flow Stats) ---
    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                if dp.id in [1, 4]:
                    parser = dp.ofproto_parser
                    # Statistiche sui FLUSSI
                    req = parser.OFPFlowStatsRequest(dp)
                    dp.send_msg(req)
            hub.sleep(self.monitor_interval)

    # --- GESTIONE RISPOSTE FLOW STATS ---
    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id

        if dpid not in [1, 4]:
            return

        current_video_bytes = 0
        
        for flow in body:
            # 1. Filtriamo per Priorità 300 (la slice Video)
            if flow.priority == 300:
                
                if 'udp_dst' in flow.match and flow.match['udp_dst'] == 9999:
                    current_video_bytes += flow.byte_count
        
        # Recuperiamo il valore precedente
        prev_bytes = self.video_stats[dpid]
        
        if prev_bytes == 0:
            delta_bytes = 0
        else:
            delta_bytes = current_video_bytes - prev_bytes
            if delta_bytes < 0: delta_bytes = 0 # Gestione reset contatori

        self.video_stats[dpid] = current_video_bytes

        # Calcolo velocità
        video_speed = delta_bytes / self.monitor_interval
        
        # Aggiorniamo le velocità correnti per il confronto globale
        if not hasattr(self, 'current_speeds'):
            self.current_speeds = {1: 0.0, 4: 0.0}
        self.current_speeds[dpid] = video_speed

        # Prendiamo il massimo tra i due switch
        max_video_speed = max(self.current_speeds[1], self.current_speeds[4])
        
        # Soglia (1 Mbps)
        is_congested = max_video_speed > self.bandwidth_threshold

        if is_congested and self.global_slice_state == 'UPPER':
            self.logger.info(f"*** VIDEO RILEVATO ({max_video_speed*8/1e6:.2f} Mbps). Traffico Standard -> LOWER.")
            self.apply_slice_policy('LOWER')
        
        elif not is_congested and self.global_slice_state == 'LOWER':
            if max_video_speed < (self.bandwidth_threshold / 2):
                self.logger.info(f"*** VIDEO TERMINATO ({max_video_speed*8/1e6:.2f} Mbps). Traffico Standard -> UPPER.")
                self.apply_slice_policy('UPPER')

    def apply_slice_policy(self, target_slice):
        self.global_slice_state = target_slice
        for dpid, dp in self.datapaths.items():
            parser = dp.ofproto_parser
            if dpid == 1:
                out_port = self.PORT_MAP[1]['s3'] if target_slice == 'LOWER' else self.PORT_MAP[1]['s2']
                for dst in ['h3', 'h4']:
                    match = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H[dst])
                    self.add_flow(dp, 250, match, [parser.OFPActionOutput(out_port)])
            elif dpid == 4:
                out_port = self.PORT_MAP[4]['s3'] if target_slice == 'LOWER' else self.PORT_MAP[4]['s2']
                for dst in ['h1', 'h2']:
                    match = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H[dst])
                    self.add_flow(dp, 250, match, [parser.OFPActionOutput(out_port)])

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                del self.datapaths[datapath.id]
                if datapath.id in self.video_stats:
                    self.video_stats[datapath.id] = 0


    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        parser = dp.ofproto_parser
        
        # Salviamo il datapath per il monitor thread
        self.datapaths[dpid] = dp


        # --- 0. REGOLA DI DEFAULT (DROP) ---
        match = parser.OFPMatch()
        self.add_flow(dp, 0, match, [])
        
# --- 1. GESTIONE ARP (Priorità 100) ---
        # Soluzione "Flood Controllato": Quando arriva un ARP, lo inoltriamo
        # a tutte le porte della Lower Slice (Host locali + S3).
        # Lo switch eviterà automaticamente di rimandarlo indietro alla porta di ingresso.
        
        # === SWITCH S1 ===
        if dpid == 1:
            # Porte coinvolte in ARP: h1(1), h2(2), s3(4). 
            # ESCLUDIAMO s2(3) che è la slice video.
            match = parser.OFPMatch(eth_type=0x0806)
            actions = [
                parser.OFPActionOutput(self.PORT_MAP[1]['h1']), # Porta 1
                parser.OFPActionOutput(self.PORT_MAP[1]['h2']), # Porta 2
                parser.OFPActionOutput(self.PORT_MAP[1]['s3'])  # Porta 4
            ]
            self.add_flow(dp, 100, match, actions)

        # === SWITCH S2 (Upper) ===
        elif dpid == 2:
            # S2 non dovrebbe mai ricevere ARP se S1/S4 lavorano bene.
            # Ma se capita, lo scartiamo (Drop implicito) o facciamo pass-through.
            # Qui non mettiamo nulla (Drop Default) per sicurezza e isolamento.
            pass

        # === SWITCH S3 (Lower Transit) ===
        elif dpid == 3:
            # S3 deve fare flood su tutte le porte attive (1 e 2)
            match = parser.OFPMatch(eth_type=0x0806)
            actions = [parser.OFPActionOutput(ofproto_v1_3.OFPP_FLOOD)]
            self.add_flow(dp, 100, match, actions)

        # === SWITCH S4 ===
        elif dpid == 4:
            # Porte coinvolte in ARP: h3(3), h4(4), s3(2).
            # ESCLUDIAMO s2(1) che è la slice video.
            match = parser.OFPMatch(eth_type=0x0806)
            actions = [
                parser.OFPActionOutput(self.PORT_MAP[4]['h3']), # Porta 3
                parser.OFPActionOutput(self.PORT_MAP[4]['h4']), # Porta 4
                parser.OFPActionOutput(self.PORT_MAP[4]['s3'])  # Porta 2
            ]
            self.add_flow(dp, 100, match, actions)


        # --- 2. SERVICE SLICING (Priorità 200 e 300) ---
        # Obiettivo: Traffico UDP porta 9999 -> Upper Slice (S2)
        #            Altro traffico IP -> Lower Slice (S3)

        # === SWITCH S1 (Ingresso traffico) ===
        if dpid == 1:
            # SLICE VIDEO (Priorità 300): UDP dst port 9999 -> Vai a S2
            # eth_type=0x0800 (IP), ip_proto=17 (UDP)
            match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, in_port=1)
            actions_video = [parser.OFPActionOutput(self.PORT_MAP[1]['s2'])]
            self.add_flow(dp, 300, match_video, actions_video)

            match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, in_port=2)
            actions_video = [parser.OFPActionOutput(self.PORT_MAP[1]['s2'])]
            self.add_flow(dp, 300, match_video, actions_video)


            # Video h2 -> h1
            match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, eth_src=self.H['h2'], eth_dst=self.H['h1'])
            actions_video = [parser.OFPActionOutput(self.PORT_MAP[1]['h1'])]
            self.add_flow(dp, 310, match_video, actions_video)

            # Video h1 -> h2
            match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, eth_src=self.H['h1'], eth_dst=self.H['h2'])
            actions_video = [parser.OFPActionOutput(self.PORT_MAP[1]['h2'])]
            self.add_flow(dp, 310, match_video, actions_video)

            # Standard H1 -> H2
            match_std_local = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H['h2'])
            actions_std_local = [parser.OFPActionOutput(self.PORT_MAP[1]['h2'])]
            self.add_flow(dp, 210, match_std_local, actions_std_local)

            # Standard H2 -> H1
            match_std_local = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H['h1'])
            actions_std_local = [parser.OFPActionOutput(self.PORT_MAP[1]['h1'])]
            self.add_flow(dp, 210, match_std_local, actions_std_local)

            # SLICE NON-VIDEO (Priorità 200): Tutto il resto IP verso H3 o H4 -> Vai a S3
            for dest_host in ['h3', 'h4']:
                match_std = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H[dest_host])
                actions_std = [parser.OFPActionOutput(self.PORT_MAP[1]['s3'])]
                self.add_flow(dp, 200, match_std, actions_std)

            # RITORNO (H3/H4 -> H1/H2): Gestiamo il traffico di ritorno che arriva da S2 o S3
            # Se arriva da S2 (Video Return) destinato a H1
            match = parser.OFPMatch(in_port=3, eth_dst=self.H['h1'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h1'])]
            self.add_flow(dp, 300, match, actions)
            
            # Se arriva da S2 (Video Return) destinato a H2
            match = parser.OFPMatch(in_port=3, eth_dst=self.H['h2'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h2'])]
            self.add_flow(dp, 300, match, actions)

            # Se arriva da S3 (Non-Video Return) destinato a H1
            match = parser.OFPMatch(in_port=4, eth_dst=self.H['h1'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h1'])]
            self.add_flow(dp, 200, match, actions)

            # Se arriva da S3 (Non-Video Return) destinato a H2
            match = parser.OFPMatch(in_port=4, eth_dst=self.H['h2'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[1]['h2'])]
            self.add_flow(dp, 200, match, actions)

        # === SWITCH S2 (Transit Upper - Video) ===
        elif dpid == 2:
            # Avanti (verso S4)
            match = parser.OFPMatch(in_port=1, eth_type=0x0800)
            actions = [parser.OFPActionOutput(2)]
            self.add_flow(dp, 300, match, actions)

            # Indietro (verso S1)
            match = parser.OFPMatch(in_port=2, eth_type=0x0800)
            actions = [parser.OFPActionOutput(1)]
            self.add_flow(dp, 300, match, actions)

        # === SWITCH S3 (Transit Lower - Non-Video) ===
        elif dpid == 3:
            # Avanti (verso S4)
            match = parser.OFPMatch(in_port=1, eth_type=0x0800)
            actions = [parser.OFPActionOutput(2)]
            self.add_flow(dp, 200, match, actions)

            # Indietro (verso S1)
            match = parser.OFPMatch(in_port=2, eth_type=0x0800)
            actions = [parser.OFPActionOutput(1)]
            self.add_flow(dp, 200, match, actions)

        # === SWITCH S4 (Egress / Merge) ===
        elif dpid == 4:
            # SLICE VIDEO (Priorità 300): UDP dst port 9999 -> Vai a S2
            # eth_type=0x0800 (IP), ip_proto=17 (UDP)
            
            # Da H3 (Porta 3) -> S2
            match = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, in_port=3)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['s2'])]
            self.add_flow(dp, 300, match, actions)

            # Da H4 (Porta 4) -> S2
            match = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, in_port=4)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['s2'])]
            self.add_flow(dp, 300, match, actions)

            # Video h3 -> h4
            match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, eth_src=self.H['h3'], eth_dst=self.H['h4'])
            actions_video = [parser.OFPActionOutput(self.PORT_MAP[4]['h4'])]
            self.add_flow(dp, 310, match_video, actions_video)

            # Video h4 -> h3
            match_video = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=9999, eth_src=self.H['h4'], eth_dst=self.H['h3'])
            actions_video = [parser.OFPActionOutput(self.PORT_MAP[4]['h3'])]
            self.add_flow(dp, 310, match_video, actions_video)

            # Standard H3 -> H4
            match_std_local = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H['h4'])
            actions_std_local = [parser.OFPActionOutput(self.PORT_MAP[4]['h4'])]
            self.add_flow(dp, 210, match_std_local, actions_std_local)

            # Standard H4 -> H3
            match_std_local = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H['h3'])
            actions_std_local = [parser.OFPActionOutput(self.PORT_MAP[4]['h3'])]
            self.add_flow(dp, 210, match_std_local, actions_std_local)

            # SLICE NON-VIDEO (Priorità 200): Tutto il resto IP verso H1 o H2 -> Vai a S3
            for dest_host in ['h1', 'h2']:
                match_std = parser.OFPMatch(eth_type=0x0800, eth_dst=self.H[dest_host])
                actions_std = [parser.OFPActionOutput(self.PORT_MAP[4]['s3'])]
                self.add_flow(dp, 200, match_std, actions_std)

            # RITORNO (H1/H2 -> H3/H4): Gestiamo il traffico di ritorno che arriva da S2 o S3
            # Se arriva da S2 (Video Return) destinato a H3
            match = parser.OFPMatch(in_port=1, eth_dst=self.H['h3'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h3'])]
            self.add_flow(dp, 300, match, actions)
            
            # Se arriva da S2 (Video Return) destinato a H4
            match = parser.OFPMatch(in_port=1, eth_dst=self.H['h4'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h4'])]
            self.add_flow(dp, 300, match, actions)

            # Se arriva da S3 (Non-Video Return) destinato a H3
            match = parser.OFPMatch(in_port=2, eth_dst=self.H['h3'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h3'])]
            self.add_flow(dp, 200, match, actions)

            # Se arriva da S3 (Non-Video Return) destinato a H4
            match = parser.OFPMatch(in_port=2, eth_dst=self.H['h4'], eth_type=0x0800)
            actions = [parser.OFPActionOutput(self.PORT_MAP[4]['h4'])]
            self.add_flow(dp, 200, match, actions)
        