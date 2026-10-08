-- A permit is an attempt budget, never stock or a purchase entitlement.
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
if not limit or limit < 1 or limit > 2147483647 or limit % 1 ~= 0
    or not window_ms or window_ms < 1 or window_ms > 2147483647 or window_ms % 1 ~= 0 then
    return {-1, 0, 0}
end

local raw = redis.call('GET', KEYS[1])
if not raw then
    redis.call('SET', KEYS[1], '1', 'PX', window_ms)
    return {1, 1, window_ms}
end

local current = tonumber(raw)
local ttl = redis.call('PTTL', KEYS[1])
-- Refuse corrupt/persistent counters; do not silently reset their budget.
if not current or current < 1 or current > 2147483647 or current % 1 ~= 0
    or tostring(current) ~= raw or ttl < 0 then
    return {-1, 0, 0}
end
if current >= limit then
    return {0, current, ttl}
end

current = redis.call('INCR', KEYS[1])
return {1, current, ttl}
