namespace PerfSentinel.RiderSmoke;

internal sealed class Program
{
    private Program() { }

    private static int SampleValue { get; set; }

    private static void Main()
    {
        SlowPath();
        SampleValue++;
        Outer();
    }

    private static void SlowPath()
    {
        Thread.Sleep(25);
    }

    private static void Outer()
    {
        void LocalWork() { }
        LocalWork();
    }

    private static void Ambiguous() { }

    private static void Ambiguous(int value) { }
}
