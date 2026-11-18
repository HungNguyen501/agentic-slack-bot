-- V14 only guards the columns with not null, so a row edited by hand in Supabase could still hold `voices` that is not a JSON
-- object, or `bindings` with a null element or more than one dimension. The read path validates every row and skips a bad one
-- (characters/core/validation.py), and these constraints refuse such a row at the source.
-- A separate migration, because V14 had already been applied: a migration that has run is never edited.
-- Adding the constraints checks the rows already stored, so a database holding a malformed row fails here until the row is fixed.

alter table public.characters
add constraint characters_bindings_check check (
    cardinality(bindings) = 0 or (array_ndims(bindings) = 1 and array_position(bindings, null) is null)
),
add constraint characters_voices_check check (jsonb_typeof(voices) = 'object');
