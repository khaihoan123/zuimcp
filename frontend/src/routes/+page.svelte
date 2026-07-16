<script lang="ts">
    // Using Svelte 5 Runes for reactivity
    let users: { email: string }[] = $state([]);
    let loading = $state(true);
    let error = $state(null);

    $effect(() => {
        fetch("/api/users")
            .then((res) => {
                if (!res.ok) throw new Error("Network response was not ok");
                return res.json();
            })
            .then((data) => {
                users = data;
                loading = false;
            })
            .catch((err) => {
                error = err.message;
                loading = false;
            });
    });
</script>

<main class="p-8 font-sans max-w-2xl mx-auto mt-10">
    <h1 class="text-3xl font-bold mb-6 text-gray-800">Platform Dashboard</h1>

    {#if loading}
        <p class="text-gray-500 animate-pulse">
            Connecting to FastAPI backend...
        </p>
    {:else if error}
        <div class="p-4 bg-red-50 border border-red-200 rounded-md">
            <p class="text-red-500 font-semibold">Connection Error: {error}</p>
        </div>
    {:else}
        <div class="p-4 bg-green-50 border border-green-200 rounded-md">
            <p class="text-green-700 font-semibold mb-2">
                ✅ Connected to PostgreSQL via FastAPI!
            </p>

            {#if users.length === 0}
                <p class="text-sm text-green-600 mt-2">
                    The database connection works perfectly, but the users table
                    is currently empty.
                </p>
            {:else}
                <ul class="list-disc pl-5 mt-4 space-y-1">
                    {#each users as user}
                        <li class="text-gray-700 font-medium">{user.email}</li>
                    {/each}
                </ul>
            {/if}
        </div>
    {/if}
</main>
