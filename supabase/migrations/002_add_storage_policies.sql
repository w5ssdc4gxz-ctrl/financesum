-- Storage policies for filings bucket
-- Note: Create the bucket first via Supabase dashboard or API

-- Allow authenticated users to upload files
INSERT INTO storage.buckets (id, name, public)
VALUES ('filings', 'filings', true)
ON CONFLICT (id) DO NOTHING;

-- Policy: Anyone can view filings
CREATE POLICY "Public Access"
ON storage.objects FOR SELECT
USING ( bucket_id = 'filings' );

-- Policy: Authenticated users can upload filings
CREATE POLICY "Authenticated users can upload filings"
ON storage.objects FOR INSERT
WITH CHECK (
    bucket_id = 'filings' 
    AND auth.role() = 'authenticated'
);

-- Policy: Authenticated users can update their uploads
CREATE POLICY "Authenticated users can update filings"
ON storage.objects FOR UPDATE
USING (
    bucket_id = 'filings'
    AND auth.role() = 'authenticated'
);

-- Policy: Authenticated users can delete filings
CREATE POLICY "Authenticated users can delete filings"
ON storage.objects FOR DELETE
USING (
    bucket_id = 'filings'
    AND auth.role() = 'authenticated'
);












