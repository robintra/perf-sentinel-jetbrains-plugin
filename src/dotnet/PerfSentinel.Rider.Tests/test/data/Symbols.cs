namespace PerfSentinel.RiderTests
{
    public sealed class Symbols
    {
        static Symbols() { }
        public Symbols() { }
        public void SlowPath() { }
        public int Count { get; set; }

        public void Outer()
        {
            void LocalWork() { }
            LocalWork();
        }
    }

    public sealed class Overloaded
    {
        public void Run() { }
        public void Run(int value) { }
    }
}
