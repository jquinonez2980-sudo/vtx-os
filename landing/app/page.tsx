import EarlyAccess from "@/components/EarlyAccess";
import Footer from "@/components/Footer";
import Hero from "@/components/Hero";
import Nav from "@/components/Nav";
import ProductFrame from "@/components/ProductFrame";
import { Agents, HowItWorks, Marquee, Trust } from "@/components/Sections";

export default function Page() {
  return (
    <main className="relative">
      <Nav />
      <Hero />
      <ProductFrame />
      <Marquee />
      <Trust />
      <HowItWorks />
      <Agents />
      <EarlyAccess />
      <Footer />
    </main>
  );
}
