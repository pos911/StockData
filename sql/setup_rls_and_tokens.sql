-- 1. Create API Tokens table
CREATE TABLE IF NOT EXISTS public.api_tokens (
    service_name text PRIMARY KEY,
    token_value text NOT NULL,
    expires_at timestamptz NOT NULL,
    updated_at timestamptz DEFAULT CURRENT_TIMESTAMP
);

-- 2. Procedure to setup RLS on all existing public tables
DO $$
DECLARE
    t_name text;
BEGIN
    FOR t_name IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') 
    LOOP
        -- Enable RLS
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', t_name);
        
        -- Drop existing policies to prevent conflicts if re-run
        EXECUTE format('DROP POLICY IF EXISTS "Allow select for everyone" ON public.%I', t_name);
        
        -- Create SELECT policy for all users (anon, authenticated, etc.)
        -- By assigning the policy to public (default), anyone can read.
        EXECUTE format('CREATE POLICY "Allow select for everyone" ON public.%I FOR SELECT USING (true);', t_name);

        -- Note: We intentionally DO NOT create policies for INSERT, UPDATE, or DELETE.
        -- In Supabase, if RLS is enabled and no explicit policy allows it, actions are denied to users.
        -- However, the 'service_role' key has super-admin privileges and Bypasses RLS completely by default.
        -- Therefore, write operations using the service_role key will succeed while all other keys/users will be blocked.
    END LOOP;
END $$;
