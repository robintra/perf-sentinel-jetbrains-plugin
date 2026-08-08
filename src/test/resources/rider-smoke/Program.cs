namespace PerfSentinel.RiderSmoke;

internal static class Program
{
    private static void Main()
    {
        SlowPath();
    }

    private static void SlowPath()
    {
        Thread.Sleep(25);
    }
}
