from mininet.topo import Topo
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info

class SliceTopo(Topo):
    def build(self):
        info('*** Creazione switch\n')
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')
        s4 = self.addSwitch('s4')

        info('*** Creazione host\n')
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')
        h4 = self.addHost('h4', ip='10.0.0.4/24', mac='00:00:00:00:00:04')

        info('*** Creazione link\n')
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(s1, s2, bw=10)
        self.addLink(s1, s3, bw=1)
        self.addLink(s2, s4, bw=10)
        self.addLink(s3, s4, bw=1)
        self.addLink(h3, s4)
        self.addLink(h4, s4)


def run():
    topo = SliceTopo()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653),
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=False
    )

    info('*** Avvio rete\n')
    net.start()

    info('*** Test con ping all\n')
    net.pingAll()

    info('*** Avvio CLI\n')
    CLI(net)

    info('*** Arresto rete\n')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
